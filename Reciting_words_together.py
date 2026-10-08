# -*- coding: utf-8 -*-
"""
一起背单词（中文 / 英文 / 日文 / 韩文 / 法文 / 西文）闪卡软件

用法：
    python Reciting_ words_ together.py

词库文件（.txt）格式：每一行「中文,拼音,英文,音标,日文,罗马音,韩文,罗马字,法文,法读音,西文,西读音」。
    单词后紧跟它的读音，读音列可以留空（写成连续的逗号即可）。
    也兼容旧的 6 列纯单词格式（此时不显示读音）。
示例：
    苹果,ping2guo3,apple,/ˈæpəl/,りんご,ringo,사과,sagwa,pomme,/pɔm/,manzana,/manˈsana/
    猫,,cat,/kæt/,ねこ,neko,고양이,goyangi,chat,/ʃa/,gato,/ˈɡato/

图片：放在程序同目录的 Pictures 文件夹里，文件名用单词命名（如 苹果.png / apple.png / りんご.png）。
      支持 png / jpg / gif（gif 可显示动图）。
      某个单词找不到对应图片时，该单词就不显示图片，只显示文字。

发音（TTS）：支持三种引擎，在「TTS 设置」里切换。
    1) HTTP（OpenAI 兼容）：连接本地语音模型服务（如 VoxCPM、Qwen3-TTS），
       POST http://<主机>:<端口>/v1/audio/speech
    2) MeloTTS（本地）：直接调用本地 MeloTTS 库，无需起服务。
       需先从 GitHub 安装（PyPI 的 melotts 包有问题）：
       pip install git+https://github.com/myshell-ai/MeloTTS.git
       （日文还需 python -m unidic download）。
    3) Edge-TTS（在线）：调用微软 Edge 在线语音，免费、无需 API Key。
       需先 pip install edge-tts。
"""
import asyncio
import base64
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']
FONT = 'Microsoft YaHei UI'

# 语种顺序 / 字段名 / 读音名称（同时用于词库解析、背面显示和图片查找）
LANG_FIELDS = [
    ('zh', '中文', '拼音'),
    ('en', '英文', '音标'),
    ('ja', '日文', '罗马音'),
    ('ko', '韩文', '罗马字'),
    ('fr', '法文', '音标'),
    ('es', '西文', '音标'),
]

# MeloTTS：语种字段 -> (MeloTTS 语言代码, 文件夹名匹配关键词)
MELO_LANG = {
    'zh': ('ZH', ('chinese', '中文', 'zh')),
    'en': ('EN', ('english',)),
    'ja': ('JP', ('japanese', '日本語', '日文', 'jp')),
    'ko': ('KR', ('korean', '한국어', '韩文', 'kr')),
    'fr': ('FR', ('french', '法语', '法文')),
    'es': ('ES', ('spanish', '西语', '西文')),
}

# MeloTTS 每个语种的默认音色名
MELO_SPEAKER = {
    'zh': 'ZH', 'en': 'EN-Default', 'ja': 'JP', 'ko': 'KR', 'fr': 'FR', 'es': 'ES',
}

# Edge-TTS 每个语种的默认音色
EDGE_VOICE = {
    'zh': 'zh-CN-XiaoxiaoNeural',
    'en': 'en-US-AriaNeural',
    'ja': 'ja-JP-NanamiNeural',
    'ko': 'ko-KR-SunHiNeural',
    'fr': 'fr-FR-DeniseNeural',
    'es': 'es-ES-ElviraNeural',
}

# 超链接地址（在这里直接修改即可）
VIDEO_URL = 'https://www.baidu.com/'   # 左下角「视频演示」链接
HELP_URL = 'https://www.baidu.com/'    # 右下角「使用说明」链接


class Word:
    __slots__ = ('zh', 'en', 'ja', 'ko', 'fr', 'es', '_reads')

    def __init__(self, zh='', en='', ja='', ko='', fr='', es='', reads=None):
        self.zh = zh
        self.en = en
        self.ja = ja
        self.ko = ko
        self.fr = fr
        self.es = es
        self._reads = reads or {}

    def get(self, lang):
        return getattr(self, lang)

    def reading(self, lang):
        return (self._reads.get(lang) or '').strip()

    def first(self):
        """返回第一个非空语种，用于列表显示。"""
        for lang, _label, _rlabel in LANG_FIELDS:
            v = getattr(self, lang)
            if v:
                return v
        return ''


def read_text(path):
    """按多种编码尝试读取文本文件。"""
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def load_words(path):
    text = read_text(path)
    words = []
    n_langs = len(LANG_FIELDS)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ',' in line or '，' in line:
            parts = [p.strip() for p in line.replace('，', ',').split(',')]
        else:
            parts = [p.strip() for p in line.split('\t')]
        n = len(parts)
        vals = {}
        reads = {}
        if n > n_langs:
            # 新格式：每个单词后跟一个读音（词,读音,词,读音,...）
            for i, (lang, _label, _rlabel) in enumerate(LANG_FIELDS):
                wi = i * 2
                vals[lang] = parts[wi] if n > wi else ''
                reads[lang] = parts[wi + 1] if n > wi + 1 else ''
        else:
            # 旧格式：只有单词，没有读音
            for i, (lang, _label, _rlabel) in enumerate(LANG_FIELDS):
                vals[lang] = parts[i] if n > i else ''
        if any(vals.values()):
            words.append(Word(**vals, reads=reads))
    return words


def find_image(root_dir, word):
    """查找某单词对应的图片，找不到返回 None。"""
    if not root_dir:
        return None
    candidates = [word.get(lang) for lang, _l, _r in LANG_FIELDS if word.get(lang)]
    for name in candidates:
        for ext in IMAGE_EXTS:
            p = os.path.join(root_dir, name + ext)
            if os.path.isfile(p):
                return p
    try:
        lower = {f.lower(): f for f in os.listdir(root_dir)}
    except OSError:
        return None
    for name in candidates:
        for ext in IMAGE_EXTS:
            key = (name + ext).lower()
            if key in lower:
                return os.path.join(root_dir, lower[key])
    return None


def word_to_line(word):
    """把 Word 序列化为词库 txt 的一行（有读音则用交错格式）。"""
    words = [word.get(lang) for lang, _l, _r in LANG_FIELDS]
    reads = [word.reading(lang) for lang, _l, _r in LANG_FIELDS]
    if any(reads):
        parts = []
        for w, r in zip(words, reads):
            parts.append(w)
            parts.append(r)
        return ','.join(parts)
    return ','.join(words)


class VocabApp:
    PROMPT_LANGS = (('中文', 'zh'), ('English', 'en'), ('日本語', 'ja'),
                    ('한국어', 'ko'), ('Français', 'fr'), ('Español', 'es'))
    ENGINE_OPTIONS = {'HTTP（OpenAI 兼容）': 'http',
                      'MeloTTS（本地）': 'melotts',
                      'Edge-TTS（在线）': 'edge'}

    def __init__(self, root):
        self.root = root
        root.title('一起背单词')
        root.geometry('820x560')
        root.minsize(660, 480)

        self.words = []
        self.order = []
        self.pos = 0
        self.flipped = False
        self.current_file = None
        self.word_dir = ''
        self.prompt_lang = tk.StringVar(value='zh')
        self.shuffle_var = tk.BooleanVar(value=False)
        self.show_read_var = tk.BooleanVar(value=True)
        self.font_family = 'Microsoft YaHei UI'
        self._photo = None
        self._melo_models = {}
        self._tts_seq = 0

        self._build_ui()
        self._bind_keys()
        self._load_tts_config()
        self.apply_font()
        self._auto_load()

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        # 菜单栏
        menubar = tk.Menu(self.root)
        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label='生成示例图片', command=self.generate_sample_images)
        menubar.add_cascade(label='工具', menu=tool_menu)
        self.root.config(menu=menubar)

        # 顶部工具栏（第一行）
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 0))
        bar.pack(side='top', fill='x')

        ttk.Button(bar, text='打开词库', command=self.open_file).pack(side='left')
        ttk.Button(bar, text='重新加载', command=self.reload).pack(side='left', padx=(6, 12))
        ttk.Button(bar, text='TTS 设置', command=self.open_tts_settings).pack(side='left', padx=(0, 6))
        ttk.Button(bar, text='字体设置', command=self.open_font_settings).pack(side='left', padx=(0, 12))
        self.file_label = ttk.Label(bar, text='未加载词库', foreground='#777')
        self.file_label.pack(side='left')
        ttk.Checkbutton(bar, text='打乱顺序', variable=self.shuffle_var,
                        command=self.on_shuffle).pack(side='right')
        ttk.Checkbutton(bar, text='显示音标', variable=self.show_read_var,
                        command=self.on_toggle_read).pack(side='right', padx=(0, 10))

        # 顶部工具栏（第二行：正面语言）
        lang_bar = ttk.Frame(self.root, padding=(10, 4, 10, 2))
        lang_bar.pack(side='top', fill='x')
        ttk.Label(lang_bar, text='正面:').pack(side='left')
        for text, val in self.PROMPT_LANGS:
            ttk.Radiobutton(lang_bar, text=text, variable=self.prompt_lang, value=val,
                            command=self.on_prompt_change).pack(side='left', padx=4)

        # 中部：列表 + 卡片
        main = ttk.Frame(self.root, padding=(10, 4, 10, 8))
        main.pack(side='top', fill='both', expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        ttk.Label(left, text='单词列表').pack(anchor='w')
        self.listbox = tk.Listbox(left, width=18, font=(FONT, 11),
                                  exportselection=False, activestyle='dotbox')
        self.listbox.pack(side='left', fill='both', expand=True, pady=(4, 0))
        sb = ttk.Scrollbar(left, orient='vertical', command=self.listbox.yview)
        sb.pack(side='right', fill='y', pady=(4, 0))
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind('<<ListboxSelect>>', self.on_list_select)

        card_area = ttk.Frame(main)
        card_area.grid(row=0, column=1, sticky='nsew')

        self.card = tk.Frame(card_area, bg='#ffffff', highlightthickness=1,
                             highlightbackground='#cccccc')
        self.card.pack(fill='both', expand=True)
        self.card.grid_columnconfigure(0, weight=1)
        self.card.grid_rowconfigure(1, weight=1)

        self.image_label = tk.Label(self.card, bg='#ffffff')
        self.image_label.grid(row=0, column=0, pady=(18, 4))

        # 正面：单词 + 下方读音（点击单词/读音 → 发音）
        self.front_frame = tk.Frame(self.card, bg='#ffffff')
        self.front_frame.grid(row=1, column=0, pady=24)
        self.front_label = tk.Label(self.front_frame, bg='#ffffff',
                                    font=(FONT, 40, 'bold'), fg='#222222',
                                    wraplength=520, justify='center', cursor='hand2')
        self.front_label.pack()
        self.front_read_label = tk.Label(self.front_frame, bg='#ffffff',
                                         font=(FONT, 20), fg='#888888',
                                         wraplength=520, justify='center', cursor='hand2')

        # 背面：六语 + 读音（每行是一个可点击发音的按钮）
        self.back_frame = tk.Frame(self.card, bg='#ffffff')
        self.back_frame.grid(row=1, column=0, pady=12, padx=20, sticky='w')
        self.back_frame.grid_remove()
        self.back_buttons = []
        for lang, label, rlabel in LANG_FIELDS:
            b = tk.Button(self.back_frame, text='', font=(FONT, 13), bg='#ffffff',
                          fg='#333333', relief='flat', anchor='w', justify='left',
                          padx=10, pady=3, cursor='hand2',
                          activebackground='#eef4ff',
                          command=lambda l=lang: self.speak(l))
            b.pack(fill='x', pady=1)
            self.back_buttons.append(b)

        self.hint_label = tk.Label(self.card, bg='#ffffff', font=(FONT, 10),
                                   fg='#999999',
                                   text='点空白 翻面 · 滚轮 切换 · 点单词发音')
        self.hint_label.grid(row=2, column=0, pady=(0, 10))

        # 点卡片空白区域 → 翻面
        for w in (self.card, self.front_frame, self.back_frame, self.image_label):
            w.bind('<Button-1>', lambda e: self.flip())
        # 点正面单词 / 读音 → 发音
        self.front_label.bind('<Button-1>', lambda e: self.speak())
        self.front_read_label.bind('<Button-1>', lambda e: self.speak())

        # 底部状态栏（超链接，最底一行）
        status = ttk.Frame(self.root, padding=(10, 2, 10, 4))
        status.pack(side='bottom', fill='x')
        self.video_link = tk.Label(status, text='视频演示', fg='#0645ad',
                                   cursor='hand2', font=(FONT, 9, 'underline'))
        self.video_link.pack(side='left')
        self.video_link.bind('<Button-1>', lambda e: self.open_link('video'))
        self.help_link = tk.Label(status, text='使用说明', fg='#0645ad',
                                  cursor='hand2', font=(FONT, 9, 'underline'))
        self.help_link.pack(side='right')
        self.help_link.bind('<Button-1>', lambda e: self.open_link('help'))

        # 底部导航
        nav = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        nav.pack(side='bottom', fill='x')
        ttk.Button(nav, text='◀ 上一张', command=lambda: self.move(-1)).pack(side='left')
        ttk.Button(nav, text='翻面', command=self.flip).pack(side='left', padx=8)
        ttk.Button(nav, text='下一张 ▶', command=lambda: self.move(1)).pack(side='left', padx=(0, 8))
        self.speak_button = ttk.Button(nav, text='🔊 发音', command=self.speak)
        self.speak_button.pack(side='left')
        self.fav_button = ttk.Button(nav, text='⭐ 收藏', command=self.toggle_favorite)
        self.fav_button.pack(side='left', padx=(8, 0))
        self.progress = ttk.Label(nav, text='0 / 0', anchor='e')
        self.progress.pack(side='right')

    def _bind_keys(self):
        self.root.bind('<space>', lambda e: self.flip())
        self.root.bind('<Right>', lambda e: self.move(1))
        self.root.bind('<Down>', lambda e: self.move(1))
        self.root.bind('<Left>', lambda e: self.move(-1))
        self.root.bind('<Up>', lambda e: self.move(-1))
        # 鼠标滚轮切换卡片（Windows / macOS）
        self.root.bind('<MouseWheel>', self.on_wheel)
        # Linux 滚轮
        self.root.bind('<Button-4>', lambda e: self.move(-1))
        self.root.bind('<Button-5>', lambda e: self.move(1))

    # ---------- 数据 ----------
    def _auto_load(self):
        here = os.path.dirname(os.path.abspath(__file__))
        default = os.path.join(here, 'words.txt')
        if os.path.isfile(default):
            self.load_file(default)

    def open_file(self):
        path = filedialog.askopenfilename(
            title='选择词库文件',
            filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if path:
            self.load_file(path)

    def reload(self):
        if self.current_file:
            self.load_file(self.current_file)
        else:
            self.open_file()

    def load_file(self, path):
        words = load_words(path)
        if not words:
            messagebox.showwarning(
                '提示', '没有读取到单词。\n'
                '请确认每行格式为：中文,拼音,英文,音标,日文,罗马音,韩文,罗马字,法文,法读音,西文,西读音\n'
                '（读音列可以留空）')
            return
        self.words = words
        self.current_file = path
        self.word_dir = os.path.dirname(os.path.abspath(path))
        self.file_label.config(text=os.path.basename(path))
        self.rebuild_order()

    def rebuild_order(self):
        n = len(self.words)
        if self.shuffle_var.get():
            self.order = list(range(n))
            random.shuffle(self.order)
        else:
            # 默认按英文单词字母序排列（忽略大小写），空英文排最后
            self.order = sorted(
                range(n),
                key=lambda i: (not (self.words[i].en or '').strip(),
                               (self.words[i].en or '').strip().lower()))
        self.pos = 0
        self.flipped = False
        self.refresh_listbox()
        self.render()

    def refresh_listbox(self):
        self.listbox.delete(0, 'end')
        for pos, i in enumerate(self.order):
            w = self.words[i]
            label = w.en or w.first() or '（空）'
            self.listbox.insert('end', f'{pos + 1}. {label}')

    # ---------- 交互 ----------
    def on_shuffle(self):
        self.rebuild_order()

    def on_toggle_read(self):
        self.render()

    def on_prompt_change(self):
        self.flipped = False
        self.render()

    def on_list_select(self, event=None):
        sel = self.listbox.curselection()
        if not sel or not self.words:
            return
        self.pos = int(sel[0])
        self.flipped = False
        self.render()

    def move(self, delta):
        if not self.words:
            return
        self.pos = (self.pos + delta) % len(self.words)
        self.flipped = False
        self.render()

    def on_wheel(self, event):
        """滚轮向上=上一张，向下=下一张。"""
        if not self.words:
            return
        self.move(-1 if event.delta > 0 else 1)

    def flip(self):
        if not self.words:
            return
        self.flipped = not self.flipped
        self.render()

    # ---------- 收藏 ----------
    def _review_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review.txt')

    def _review_lines(self):
        p = self._review_path()
        if not os.path.isfile(p):
            return []
        return read_text(p).splitlines()

    def _in_review(self, word):
        line = word_to_line(word)
        return any(l.strip() == line for l in self._review_lines())

    def toggle_favorite(self):
        if not self.words:
            return
        w = self.words[self.order[self.pos]]
        line = word_to_line(w)
        lines = [l for l in self._review_lines() if l.strip()]
        if line in lines:
            lines.remove(line)
            self.fav_button.config(text='⭐ 收藏')
        else:
            lines.append(line)
            self.fav_button.config(text='⭐ 已收藏')
        try:
            with open(self._review_path(), 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + ('\n' if lines else ''))
        except Exception as e:
            messagebox.showerror('收藏失败', f'无法写入 review.txt：{e}')

    # ---------- 图片目录 / 超链接 ----------
    def _pictures_dir(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Pictures')

    def open_link(self, kind):
        url = VIDEO_URL if kind == 'video' else HELP_URL
        if not url:
            messagebox.showinfo('提示', '尚未设置链接，请在 vocab_app.py 里修改 VIDEO_URL / HELP_URL')
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror('打开失败', str(e))

    # ---------- 字体 / 示例图片 ----------
    def apply_font(self):
        f = self.font_family
        self.listbox.config(font=(f, 11))
        self.front_label.config(font=(f, 40, 'bold'))
        self.front_read_label.config(font=(f, 20))
        for b in self.back_buttons:
            b.config(font=(f, 13))
        self.hint_label.config(font=(f, 10))
        self.video_link.config(font=(f, 9, 'underline'))
        self.help_link.config(font=(f, 9, 'underline'))

    def open_font_settings(self):
        import tkinter.font as tkfont
        families = sorted(set(tkfont.families(self.root)))
        dlg = tk.Toplevel(self.root)
        dlg.title('字体设置')
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        f = ttk.Frame(dlg, padding=12)
        f.pack(fill='both', expand=True)

        ttk.Label(f, text='选择系统字体:').pack(anchor='w')
        list_frame = ttk.Frame(f)
        list_frame.pack(fill='both', expand=True, pady=(4, 0))
        box = tk.Listbox(list_frame, height=16, width=36, exportselection=False)
        box.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(list_frame, orient='vertical', command=box.yview)
        sb.pack(side='right', fill='y')
        box.config(yscrollcommand=sb.set)
        for fam in families:
            box.insert('end', fam)
        if self.font_family in families:
            idx = families.index(self.font_family)
            box.selection_set(idx)
            box.see(idx)

        preview = tk.Label(f, text='预览：Abc 苹果 りんご 123', font=(self.font_family, 18))
        preview.pack(anchor='w', pady=(8, 4))

        def on_select(*_a):
            sel = box.curselection()
            if sel:
                preview.config(font=(families[sel[0]], 18))
        box.bind('<<ListboxSelect>>', on_select)

        def save():
            sel = box.curselection()
            if sel:
                self.font_family = families[sel[0]]
            self._save_tts_config()
            self.apply_font()
            dlg.destroy()

        btns = ttk.Frame(f)
        btns.pack(pady=(4, 0))
        ttk.Button(btns, text='保存', command=save).pack(side='left', padx=(0, 6))
        ttk.Button(btns, text='取消', command=dlg.destroy).pack(side='left')

    def generate_sample_images(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            messagebox.showwarning('提示', '需要 Pillow 才能生成示例图片，请先 pip install pillow')
            return
        out = self._pictures_dir()
        os.makedirs(out, exist_ok=True)
        items = [
            ('apple.png', 'apple', '#e74c3c'),
            ('banana.png', 'banana', '#f1c40f'),
            ('cat.png', 'cat', '#8e44ad'),
        ]
        fnt = self._sample_font(ImageFont, 60)
        made = []
        for name, text, color in items:
            img = Image.new('RGB', (320, 240), color)
            d = ImageDraw.Draw(img)
            bbox = d.textbbox((0, 0), text, font=fnt)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text(((320 - tw) / 2 - bbox[0], (240 - th) / 2 - bbox[1]),
                   text, fill='white', font=fnt)
            img.save(os.path.join(out, name))
            made.append(name)
        messagebox.showinfo('完成', '已生成示例图片：\n' + '\n'.join(made) + f'\n保存到：{out}')

    @staticmethod
    def _sample_font(ImageFont, size):
        for p in (r'C:\Windows\Fonts\arial.ttf',
                  r'C:\Windows\Fonts\segoeui.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
            if os.path.isfile(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    # ---------- 渲染 ----------
    def render(self):
        if self.words:
            self.progress.config(text=f'{self.pos + 1} / {len(self.words)}')
        else:
            self.progress.config(text='0 / 0')
        self._sync_selection()

        if not self.words:
            self._hide_image()
            self.back_frame.grid_remove()
            self.front_frame.grid()
            self.front_label.config(text='请先打开词库文件')
            self.front_read_label.pack_forget()
            self.fav_button.config(text='⭐ 收藏')
            return

        w = self.words[self.order[self.pos]]
        self.fav_button.config(text='⭐ 已收藏' if self._in_review(w) else '⭐ 收藏')
        img_path = find_image(self._pictures_dir(), w)
        self._photo = self._load_photo(img_path, 300, 230) if img_path else None
        if self._photo:
            self.image_label.config(image=self._photo, text='')
            self.image_label.grid()
        else:
            self._hide_image()

        if self.flipped:
            self.front_frame.grid_remove()
            show_read = self.show_read_var.get()
            for b, (lang, label, _rlabel) in zip(self.back_buttons, LANG_FIELDS):
                word = w.get(lang) or '（空）'
                read = w.reading(lang)
                txt = f'🔊 {label}：{word}'
                if read and show_read:
                    txt += f'  ({read})'
                b.config(text=txt)
            self.back_frame.grid()
        else:
            self.back_frame.grid_remove()
            self.front_frame.grid()
            lang = self.prompt_lang.get()
            self.front_label.config(text=w.get(lang) or '（空）')
            read = w.reading(lang)
            if read and self.show_read_var.get():
                self.front_read_label.config(text=read)
                self.front_read_label.pack(pady=(6, 0))
            else:
                self.front_read_label.pack_forget()

    def _hide_image(self):
        self.image_label.config(image='', text='')
        self.image_label.grid_remove()

    def _sync_selection(self):
        if not self.words:
            return
        self.listbox.selection_clear(0, 'end')
        self.listbox.selection_set(self.pos)
        self.listbox.see(self.pos)

    def _load_photo(self, path, max_w, max_h):
        ext = os.path.splitext(path)[1].lstrip('.').lower()
        # GIF 动图：用 tk.PhotoImage 直接加载以保留动画（不缩放）
        if ext == 'gif':
            try:
                return tk.PhotoImage(file=path)
            except Exception:
                return None
        if HAS_PIL:
            try:
                img = Image.open(path)
                resample = getattr(Image, 'LANCZOS',
                                   getattr(Image, 'ANTIALIAS', 1))
                img.thumbnail((max_w, max_h), resample)
                return ImageTk.PhotoImage(img)
            except Exception:
                return None
        try:
            photo = tk.PhotoImage(file=path)
        except Exception:
            return None
        w, h = photo.width(), photo.height()
        if w > max_w or h > max_h:
            factor = max(1, int(round(max(w / max_w, h / max_h))))
            photo = photo.subsample(factor, factor)
        return photo

    # ---------- TTS 配置 ----------
    def _tts_config_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tts_config.json')

    def _load_tts_config(self):
        self.tts_mode = 'http'
        self.tts_base = 'http://localhost:8080'
        self.tts_model = 'voxcpm-1.5'
        self.tts_voice = ''
        self.tts_key = ''
        self.tts_format = 'wav'
        self.tts_melo_device = 'auto'
        self.tts_melo_dir = ''
        self.tts_edge_voices = dict(EDGE_VOICE)
        self.tts_edge_rate = '+0%'
        self.font_family = 'Microsoft YaHei UI'
        try:
            with open(self._tts_config_path(), 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            return
        if isinstance(cfg, dict):
            for k in ('mode', 'base', 'model', 'voice', 'key', 'format',
                      'melo_device', 'melo_dir', 'edge_rate'):
                if cfg.get(k):
                    setattr(self, 'tts_' + k, cfg[k])
            ev = cfg.get('edge_voices')
            if isinstance(ev, dict):
                for lang, _label, _r in LANG_FIELDS:
                    if ev.get(lang):
                        self.tts_edge_voices[lang] = ev[lang]
            if cfg.get('font'):
                self.font_family = cfg['font']

    def _save_tts_config(self):
        cfg = {'mode': self.tts_mode, 'base': self.tts_base,
               'model': self.tts_model, 'voice': self.tts_voice,
               'key': self.tts_key, 'format': self.tts_format,
               'melo_device': self.tts_melo_device, 'melo_dir': self.tts_melo_dir,
               'edge_rate': self.tts_edge_rate, 'edge_voices': self.tts_edge_voices,
               'font': self.font_family}
        try:
            with open(self._tts_config_path(), 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def open_tts_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title('TTS 语音设置')
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        f = ttk.Frame(dlg, padding=12)
        f.pack(fill='both', expand=True)

        # 引擎选择
        ttk.Label(f, text='发音引擎').grid(row=0, column=0, sticky='e', padx=4, pady=4)
        disp = {v: k for k, v in self.ENGINE_OPTIONS.items()}
        mode_var = tk.StringVar(value=disp.get(self.tts_mode, 'HTTP（OpenAI 兼容）'))
        ttk.Combobox(f, textvariable=mode_var, state='readonly', width=24,
                     values=list(self.ENGINE_OPTIONS.keys())).grid(
                         row=0, column=1, sticky='w', padx=4, pady=4)

        # HTTP 设置区
        http_frame = ttk.LabelFrame(f, text='HTTP（OpenAI 兼容）', padding=8)
        http_frame.grid(row=1, column=0, columnspan=2, sticky='ew', padx=4, pady=4)
        rows = [
            ('接口地址', 'base', self.tts_base),
            ('模型名称', 'model', self.tts_model),
            ('音色 voice', 'voice', self.tts_voice),
            ('API Key（可选）', 'key', self.tts_key),
        ]
        http_vars = {}
        for i, (label, k, default) in enumerate(rows):
            ttk.Label(http_frame, text=label).grid(row=i, column=0, sticky='e', padx=4, pady=3)
            v = tk.StringVar(value=default)
            ttk.Entry(http_frame, textvariable=v, width=44).grid(row=i, column=1, padx=4, pady=3)
            http_vars[k] = v
        ttk.Label(http_frame, text='音频格式').grid(row=len(rows), column=0, sticky='e', padx=4, pady=3)
        fmt = tk.StringVar(value=self.tts_format)
        ttk.Combobox(http_frame, textvariable=fmt, width=14, state='readonly',
                     values=('wav', 'mp3', 'flac', 'opus', 'aac')).grid(
                         row=len(rows), column=1, sticky='w', padx=4, pady=3)
        http_vars['format'] = fmt

        # MeloTTS 设置区
        melo_frame = ttk.LabelFrame(f, text='MeloTTS（本地）', padding=8)
        melo_frame.grid(row=1, column=0, columnspan=2, sticky='ew', padx=4, pady=4)
        ttk.Label(melo_frame, text='设备').grid(row=0, column=0, sticky='e', padx=4, pady=3)
        melo_device = tk.StringVar(value=self.tts_melo_device)
        ttk.Combobox(melo_frame, textvariable=melo_device, state='readonly', width=14,
                     values=('auto', 'cpu', 'cuda')).grid(row=0, column=1, sticky='w', padx=4, pady=3)
        ttk.Label(melo_frame, text='模型目录').grid(row=1, column=0, sticky='e', padx=4, pady=3)
        melo_dir = tk.StringVar(value=self.tts_melo_dir)
        ttk.Entry(melo_frame, textvariable=melo_dir, width=44).grid(row=1, column=1, padx=4, pady=3)
        ttk.Label(melo_frame, text='留空 = 脚本同目录的 MeloTTS 文件夹',
                  foreground='#888').grid(row=2, column=0, columnspan=2, sticky='w', padx=4)
        ttk.Button(melo_frame, text='检测模型', command=self.check_melo_models).grid(
            row=3, column=0, columnspan=2, sticky='w', padx=4, pady=4)

        # Edge-TTS 设置区
        edge_frame = ttk.LabelFrame(f, text='Edge-TTS（在线）', padding=8)
        edge_frame.grid(row=1, column=0, columnspan=2, sticky='ew', padx=4, pady=4)
        edge_voice_vars = {}
        for i, (lang, label, _r) in enumerate(LANG_FIELDS):
            ttk.Label(edge_frame, text=label).grid(row=i, column=0, sticky='e', padx=4, pady=2)
            v = tk.StringVar(value=self.tts_edge_voices.get(lang, EDGE_VOICE.get(lang, '')))
            ttk.Entry(edge_frame, textvariable=v, width=30).grid(row=i, column=1, padx=4, pady=2)
            edge_voice_vars[lang] = v
        ttk.Label(edge_frame, text='语速(如 +10%)').grid(row=6, column=0, sticky='e', padx=4, pady=2)
        edge_rate = tk.StringVar(value=self.tts_edge_rate)
        ttk.Entry(edge_frame, textvariable=edge_rate, width=10).grid(row=6, column=1, sticky='w', padx=4, pady=2)

        def update_visible(*_a):
            http_frame.grid_remove()
            melo_frame.grid_remove()
            edge_frame.grid_remove()
            m = mode_var.get()
            if m == 'MeloTTS（本地）':
                melo_frame.grid()
            elif m == 'Edge-TTS（在线）':
                edge_frame.grid()
            else:
                http_frame.grid()
        mode_var.trace_add('write', update_visible)
        update_visible()

        def save():
            self.tts_mode = self.ENGINE_OPTIONS[mode_var.get()]
            self.tts_base = http_vars['base'].get().strip().rstrip('/')
            self.tts_model = http_vars['model'].get().strip()
            self.tts_voice = http_vars['voice'].get().strip()
            self.tts_key = http_vars['key'].get().strip()
            self.tts_format = fmt.get()
            self.tts_melo_device = melo_device.get()
            self.tts_melo_dir = melo_dir.get().strip()
            self.tts_edge_rate = edge_rate.get().strip() or '+0%'
            for lang, v in edge_voice_vars.items():
                self.tts_edge_voices[lang] = v.get().strip()
            self._save_tts_config()
            dlg.destroy()

        btns = ttk.Frame(f)
        btns.grid(row=2, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text='保存', command=save).pack(side='left', padx=4)
        ttk.Button(btns, text='取消', command=dlg.destroy).pack(side='left', padx=4)

        ttk.Label(f, text='Edge-TTS 需先 pip install edge-tts；MeloTTS 从 GitHub 安装',
                  foreground='#888').grid(row=3, column=0, columnspan=2, pady=(8, 0))

    # ---------- 发音 ----------
    def speak(self, lang=None):
        """朗读当前单词。lang 为 None 时读「正面」所选语言。"""
        if not self.words:
            return
        if lang is None:
            lang = self.prompt_lang.get()
        w = self.words[self.order[self.pos]]
        text = w.get(lang)
        if not text:
            messagebox.showinfo('提示', '该语种没有文字，无法发音。')
            return
        self.speak_button.config(text='🔊 发音中…')
        if self.tts_mode == 'melotts':
            threading.Thread(target=self._melo_speak, args=(lang, text), daemon=True).start()
        elif self.tts_mode == 'edge':
            threading.Thread(target=self._edge_speak, args=(lang, text), daemon=True).start()
        else:
            threading.Thread(target=self._fetch_tts, args=(text,), daemon=True).start()

    def _reset_speak_button(self):
        self.speak_button.config(text='🔊 发音')

    def _next_tmp(self, ext):
        """生成唯一临时音频文件名，避免与正在播放（被 MCI 锁定）的文件冲突。"""
        self._tts_seq += 1
        return os.path.join(tempfile.gettempdir(), f'vocab_{self._tts_seq}.{ext}')

    # ----- HTTP 引擎 -----
    def _fetch_tts(self, text):
        try:
            data, fmt = self._tts_request(text)
        except Exception as e:
            err = str(e)
            self.root.after(0, self._reset_speak_button)
            self.root.after(0, lambda: messagebox.showerror(
                '发音失败', f'{err}\n\n请检查 TTS 接口地址、模型名和音色是否正确。'))
            return
        if not data:
            self.root.after(0, self._reset_speak_button)
            self.root.after(0, lambda: messagebox.showerror(
                '发音失败', '未能从响应中解析出音频。'))
            return
        self.root.after(0, lambda: self._play_audio(data, fmt))

    def _tts_request(self, text):
        url = self.tts_base.rstrip('/') + '/v1/audio/speech'
        payload = {'model': self.tts_model, 'input': text}
        if self.tts_voice:
            payload['voice'] = self.tts_voice
        if self.tts_format:
            payload['response_format'] = self.tts_format
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        if self.tts_key:
            req.add_header('Authorization', 'Bearer ' + self.tts_key)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            ctype = resp.headers.get('Content-Type', '')
        data = self._extract_audio(data, ctype)
        return data, self.tts_format

    @staticmethod
    def _extract_audio(data, ctype):
        """若返回的是 JSON（如 DashScope 风格），尝试提取 base64 音频；否则当作原始音频。"""
        if not data:
            return b''
        if data[:1] == b'{' or 'json' in (ctype or '').lower():
            try:
                obj = json.loads(data.decode('utf-8'))
            except Exception:
                return data
            cands = []
            out = obj.get('output')
            if isinstance(out, dict):
                a = out.get('audio')
                if isinstance(a, dict):
                    cands += [a.get('data'), a.get('b64'), a.get('base64')]
            for k in ('data', 'audio', 'base64'):
                v = obj.get(k)
                if isinstance(v, str):
                    cands.append(v)
            for c in cands:
                if c:
                    try:
                        return base64.b64decode(c)
                    except Exception:
                        pass
        return data

    # ----- MeloTTS 引擎 -----
    def _melo_dir_default(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'MeloTTS')

    def _melo_root(self):
        return self.tts_melo_dir or self._melo_dir_default()

    def _find_melo_model(self, lang):
        """返回 (语言代码, config.json 路径, checkpoint.pth 路径)。"""
        code, keys = MELO_LANG.get(lang, (lang.upper(), (lang,)))
        root = self._melo_root()
        if not os.path.isdir(root):
            return code, None, None
        folder = None
        for d in sorted(os.listdir(root)):
            full = os.path.join(root, d)
            if not os.path.isdir(full):
                continue
            dl = d.lower()
            if any(k.lower() in dl for k in keys):
                folder = full
                break
        if folder is None:
            return code, None, None
        cfg = os.path.join(folder, 'config.json')
        if not os.path.isfile(cfg):
            cfg = None
        ckpt = None
        cand = os.path.join(folder, 'checkpoint.pth')
        if os.path.isfile(cand):
            ckpt = cand
        else:
            try:
                for fn in sorted(os.listdir(folder)):
                    if fn.lower().endswith('.pth'):
                        ckpt = os.path.join(folder, fn)
                        break
            except OSError:
                pass
        return code, cfg, ckpt

    def check_melo_models(self):
        lines = []
        for lang, label, _rlabel in LANG_FIELDS:
            code, cfg, ckpt = self._find_melo_model(lang)
            ok = bool(cfg and ckpt)
            lines.append(f'{label}（{code}）：{"✓ 已找到" if ok else "✗ 未找到"}')
        messagebox.showinfo('MeloTTS 模型检测', '\n'.join(lines))

    def _get_melo_model(self, lang):
        if lang in self._melo_models:
            return self._melo_models[lang]
        try:
            from melo.api import TTS
        except Exception as e:
            raise RuntimeError(f'未安装 MeloTTS：{e}\n\n'
                               f'PyPI 上的 melotts 包有问题，请从 GitHub 安装：\n'
                               f'pip install git+https://github.com/myshell-ai/MeloTTS.git')
        code, cfg, ckpt = self._find_melo_model(lang)
        if not cfg or not ckpt:
            label = dict((f, l) for f, l, _ in LANG_FIELDS).get(lang, lang)
            raise RuntimeError(f'未找到「{label}」的 MeloTTS 模型（需 config.json 和 checkpoint.pth）\n'
                               f'请把下载的模型文件夹放进：{self._melo_root()}')
        device = self.tts_melo_device or 'auto'
        model = TTS(language=code, device=device,
                    config_path=cfg, ckpt_path=ckpt)
        self._melo_models[lang] = model
        return model

    def _melo_speaker_id(self, lang, model):
        try:
            spk2id = model.hps.data.spk2id
        except Exception:
            return 0
        name = MELO_SPEAKER.get(lang, '')
        if name and name in spk2id:
            return spk2id[name]
        if spk2id:
            return next(iter(spk2id.values()))
        return 0

    def _melo_speak(self, lang, text):
        try:
            model = self._get_melo_model(lang)
            sid = self._melo_speaker_id(lang, model)
            out = self._next_tmp('wav')
            try:
                model.tts_to_file(text, sid, out, quiet=True)
            except TypeError:
                model.tts_to_file(text, sid, out)
        except Exception as e:
            err = str(e)
            self.root.after(0, self._reset_speak_button)
            self.root.after(0, lambda: messagebox.showerror('发音失败', f'{err}'))
            return
        self.root.after(0, lambda: self._play_audio_file(out))

    # ----- Edge-TTS 引擎 -----
    def _edge_speak(self, lang, text):
        try:
            import edge_tts
        except Exception as e:
            err = str(e)
            self.root.after(0, self._reset_speak_button)
            self.root.after(0, lambda: messagebox.showerror(
                '发音失败', f'未安装 edge-tts：{err}\n\n请先执行 pip install edge-tts'))
            return
        voice = self.tts_edge_voices.get(lang) or EDGE_VOICE.get(lang, 'en-US-AriaNeural')
        rate = (self.tts_edge_rate or '+0%').strip()
        out = self._next_tmp('mp3')
        try:
            asyncio.run(self._edge_synth(edge_tts, text, voice, rate, out))
        except Exception as e:
            err = str(e)
            self.root.after(0, self._reset_speak_button)
            self.root.after(0, lambda: messagebox.showerror(
                '发音失败', f'Edge-TTS 合成失败\n音色：{voice}\n文本：{text}\n错误：{err}'))
            return
        self.root.after(0, lambda: self._play_audio_file(out))

    async def _edge_synth(self, edge_tts_mod, text, voice, rate, out):
        communicate = edge_tts_mod.Communicate(text, voice=voice, rate=rate)
        await communicate.save(out)

    # ----- 播放 -----
    def _play_audio(self, data, fmt):
        ext_map = {'wav': 'wav', 'mp3': 'mp3', 'flac': 'flac',
                   'opus': 'ogg', 'aac': 'aac'}
        ext = ext_map.get(fmt, 'wav')
        path = self._next_tmp(ext)
        try:
            with open(path, 'wb') as f:
                f.write(data)
        except Exception as e:
            messagebox.showerror('发音失败', f'无法写入临时音频：{e}')
            return
        self._play_audio_file(path)

    def _play_audio_file(self, path):
        self._reset_speak_button()
        ext = os.path.splitext(path)[1].lstrip('.').lower()
        # 1) Windows 自带 winsound（仅 WAV）
        if ext == 'wav':
            try:
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except Exception:
                pass
        # 2) Windows MCI（mp3 等，不弹窗、无需额外库）
        if sys.platform == 'win32':
            try:
                if self._play_mci(path):
                    return
            except Exception:
                pass
        # 3) pygame（若已安装）
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            return
        except Exception:
            pass
        # 4) 系统默认播放器
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['afplay', path])
            else:
                cmd = ['aplay', path] if shutil.which('aplay') else ['xdg-open', path]
                subprocess.Popen(cmd)
            return
        except Exception as e:
            messagebox.showerror('发音失败', f'无法播放音频：{e}')

    @staticmethod
    def _play_mci(path):
        """用 Windows MCI 播放 mp3，返回是否成功。"""
        import ctypes
        w = ctypes.windll.winmm
        w.mciSendStringW('close vocabtts', None, 0, None)
        r = w.mciSendStringW(f'open "{path}" type mpegvideo alias vocabtts', None, 0, None)
        if r != 0:
            r = w.mciSendStringW(f'open "{path}" alias vocabtts', None, 0, None)
        if r != 0:
            return False
        if w.mciSendStringW('play vocabtts', None, 0, None) != 0:
            w.mciSendStringW('close vocabtts', None, 0, None)
            return False
        return True


def main():
    root = tk.Tk()
    VocabApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
