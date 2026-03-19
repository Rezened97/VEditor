import os
import sys
import re
import subprocess
from pathlib import Path
from itertools import product
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Drag & Drop support
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Errore", "Installa tkinterdnd2: pip install tkinterdnd2")
    root.destroy()
    sys.exit(1)

# Use imageio-ffmpeg bundled ffmpeg binary
try:
    from imageio_ffmpeg import get_ffmpeg_exe
    ffmpeg_bin = get_ffmpeg_exe()
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Errore", "Installa imageio-ffmpeg: pip install imageio-ffmpeg")
    root.destroy()
    sys.exit(1)

# Prepare startupinfo to hide ffmpeg console on Windows
STARTUPINFO = None
if os.name == 'nt':
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    STARTUPINFO = si

# -------- Costanti UI Moderne --------
BG_APP = "#F3F4F6"
BG_CARD = "#FFFFFF"
COLOR_ACCENT = "#3B82F6"
COLOR_TEXT = "#1F2937"
COLOR_DANGER = "#EF4444"
FONT_MAIN = ('Segoe UI', 10)
FONT_TITLE = ('Segoe UI', 12, 'bold')
FONT_LOG = ('Consolas', 9)


# -------- Helpers per durata e speed --------
def get_media_duration(path: Path) -> float:
    cmd = [ffmpeg_bin, '-i', str(path)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=STARTUPINFO)
    _, err = proc.communicate()
    text = err.decode(errors='ignore')
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if not m: return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def adjust_audio_speed(audio: Path, target_duration: float) -> Path:
    orig = get_media_duration(audio)
    if orig <= 0 or target_duration <= 0: return audio

    speed = orig / target_duration
    filters = []
    factor = speed

    while factor < 0.5:
        filters.append('atempo=0.5')
        factor /= 0.5
    while factor > 2.0:
        filters.append('atempo=2.0')
        factor /= 2.0

    filters.append(f'atempo={factor}')
    filt = ','.join(filters)

    out_file = audio.with_name(f"{audio.stem}_adj{audio.suffix}")
    cmd = [ffmpeg_bin, '-y', '-i', str(audio), '-filter:a', filt, str(out_file)]
    subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
    return out_file


# -------- Process functions (Metodo TS: Veloce e Senza Blocchi) --------
def process_concat_internal(inputs, output: Path):
    temp_ts_files = []
    
    # 1. Converti rapidamente ogni clip in .ts per resettare i timestamp (evita il blocco del video)
    for i, p in enumerate(inputs):
        ts = output.with_name(f"{output.stem}_temp_{i}.ts")
        cmd = [
            ffmpeg_bin, '-y', '-i', str(p),
            '-c:v', 'copy',                           # Copia video istantanea (nessun rendering)
            '-c:a', 'aac', '-ar', '44100', '-ac', '2',# Uniforma l'audio per evitare conflitti
            '-f', 'mpegts', str(ts)                   # Formato TS per ripulire la timeline
        ]
        subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
        temp_ts_files.append(ts)

    # 2. Crea il file txt per il concat dei file .ts
    list_file = output.with_name(f"{output.stem}_list.txt")
    with open(list_file, 'w', encoding='utf-8') as f:
        for ts in temp_ts_files:
            f.write(f"file '{ts.resolve().as_posix()}'\n")

    # 3. Unisci i file .ts e impacchetta di nuovo in .mp4 (Velocissimo)
    cmd_concat = [
        ffmpeg_bin, '-y', '-f', 'concat', '-safe', '0',
        '-i', str(list_file),
        '-c', 'copy',
        str(output)
    ]
    subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

    # Pulizia
    if list_file.exists(): list_file.unlink()
    for ts in temp_ts_files:
        if ts.exists(): ts.unlink()


def process_concat_external(inputs, audio: Path, output: Path):
    temp_ts_files = []
    
    # 1. Converti rapidamente in .ts rimuovendo l'audio
    for i, p in enumerate(inputs):
        ts = output.with_name(f"{output.stem}_temp_{i}.ts")
        cmd = [
            ffmpeg_bin, '-y', '-i', str(p),
            '-c:v', 'copy',     # Copia istantanea video
            '-an',              # Rimuove l'audio
            '-f', 'mpegts', str(ts)
        ]
        subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
        temp_ts_files.append(ts)

    list_file = output.with_name(f"{output.stem}_list.txt")
    with open(list_file, 'w', encoding='utf-8') as f:
        for ts in temp_ts_files:
            f.write(f"file '{ts.resolve().as_posix()}'\n")

    # 2. Unisci il video muto (Velocissimo)
    temp_vid = output.with_name(f"{output.stem}_v.mp4")
    cmd_concat = [
        ffmpeg_bin, '-y', '-f', 'concat', '-safe', '0',
        '-i', str(list_file),
        '-c', 'copy',
        str(temp_vid)
    ]
    subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

    # 3. Aggiungi l'audio esterno regolato
    total_dur = sum(get_media_duration(p) for p in inputs)
    adj_audio = adjust_audio_speed(audio, total_dur)

    subprocess.run([
        ffmpeg_bin, '-y',
        '-i', str(temp_vid),
        '-i', str(adj_audio),
        '-map', '0:v:0',
        '-map', '1:a:0',
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-shortest',
        str(output)
    ], check=True, startupinfo=STARTUPINFO)

    # Pulizia
    if list_file.exists(): list_file.unlink()
    if temp_vid.exists(): temp_vid.unlink()
    if adj_audio != audio and adj_audio.exists():
        try: adj_audio.unlink()
        except OSError: pass
    for ts in temp_ts_files:
        if ts.exists(): ts.unlink()


# -------- GUI classes --------
class FileList(ttk.Frame):
    def __init__(self, parent, title, filetypes):
        super().__init__(parent, style='Card.TFrame')
        self.filetypes = filetypes
        self.storage = []
        self.enabled = True

        header_frame = ttk.Frame(self, style='Card.TFrame')
        header_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        ttk.Label(header_frame, text=title, font=FONT_TITLE, background=BG_CARD, foreground=COLOR_TEXT).pack(side='left')
        self.load_btn = ttk.Button(header_frame, text='+ Aggiungi', width=10, command=self.load_files, style='Outline.TButton')
        self.load_btn.pack(side='right')

        self.drop_area = tk.Label(self, text='📁 Trascina i file qui', font=('Segoe UI', 10, 'italic'), bg='#F9FAFB', fg='#9CA3AF', relief='solid', borderwidth=1, height=3)
        self.drop_area.pack(fill='x', padx=10, pady=5)
        self.drop_area.drop_target_register(DND_FILES)
        self.drop_area.dnd_bind('<<Drop>>', self.handle_drop)

        self.list_frame = tk.Frame(self, bg=BG_CARD)
        self.list_frame.pack(fill='x', padx=10, pady=(0, 10))
        self.list_frame.columnconfigure(0, weight=1)

    def load_files(self):
        if not self.enabled: return
        files = filedialog.askopenfilenames(filetypes=self.filetypes)
        self.add_files(files)

    def handle_drop(self, event):
        if not self.enabled: return
        self.add_files(self.tk.splitlist(event.data))

    def add_files(self, files):
        if not self.enabled: return
        changed = False
        for f in files:
            p = Path(f)
            if p.is_file() and p not in self.storage:
                self.storage.append(p)
                changed = True
        if changed:
            self.refresh_list()

    def remove_file(self, index):
        if 0 <= index < len(self.storage):
            del self.storage[index]
            self.refresh_list()

    def refresh_list(self):
        for w in self.list_frame.winfo_children(): w.destroy()
        for idx, p in enumerate(self.storage):
            row_bg = '#F3F4F6' if idx % 2 == 0 else BG_CARD
            row = tk.Frame(self.list_frame, bg=row_bg, pady=4, padx=5)
            row.pack(fill='x', pady=1)
            tk.Label(row, text=p.name, bg=row_bg, fg=COLOR_TEXT, font=FONT_MAIN, anchor='w').pack(side='left', fill='x', expand=True)
            del_btn = tk.Button(row, text='✕', bg=row_bg, fg=COLOR_DANGER, bd=0, font=('Segoe UI', 10, 'bold'), activebackground=row_bg, activeforeground='#B91C1C', cursor='hand2', command=lambda i=idx: self.remove_file(i))
            del_btn.pack(side='right', padx=5)
            if not self.enabled: del_btn.config(state='disabled')

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        state = 'normal' if enabled else 'disabled'
        self.load_btn.configure(state=state)
        if enabled: self.drop_area.configure(bg='#F9FAFB', fg='#6B7280', text='📁 Trascina i file qui')
        else: self.drop_area.configure(bg='#E5E7EB', fg='#9CA3AF', text='🚫 Sezione Disattivata')
        self.refresh_list()


class MontageGUI(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title('Automazione Montaggio Video')
        self.geometry('750x850')
        self.configure(bg=BG_APP)
        self.minsize(600, 700)
        self.setup_styles()

        self.mode_var = tk.StringVar(value='I')
        self.use_lead_var = tk.BooleanVar(value=True)

        self.output_dir = Path.cwd() / 'video_finali'
        self.output_dir.mkdir(exist_ok=True)

        header = tk.Frame(self, bg=COLOR_ACCENT, height=60)
        header.pack(fill='x')
        tk.Label(header, text="🎬 Generatore Video Multiplo", bg=COLOR_ACCENT, fg="white", font=('Segoe UI', 16, 'bold')).pack(pady=15)

        container = ttk.Frame(self)
        container.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(container, bg=BG_APP, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

        self.scroll_frame = tk.Frame(self.canvas, bg=BG_APP)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor='nw')

        self.scroll_frame.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._on_mousewheel)

        self.build_ui()

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=FONT_MAIN, background=BG_APP)
        style.configure('Card.TFrame', background=BG_CARD)
        style.configure('TRadiobutton', background=BG_APP, font=FONT_MAIN)
        style.configure('TCheckbutton', background=BG_APP, font=FONT_MAIN)
        style.configure('TButton', font=FONT_MAIN, padding=5)
        style.configure('Outline.TButton', background=BG_CARD, foreground=COLOR_TEXT)
        style.configure('Accent.TButton', font=('Segoe UI', 12, 'bold'), background=COLOR_ACCENT, foreground='white', padding=10)
        style.map('Accent.TButton', background=[('active', '#2563EB')])

    def build_ui(self):
        content = tk.Frame(self.scroll_frame, bg=BG_APP)
        content.pack(fill='both', expand=True, padx=20, pady=15)

        config_frame = tk.LabelFrame(content, text=" Impostazioni di Montaggio ", font=FONT_TITLE, bg=BG_APP, fg=COLOR_TEXT, padx=15, pady=10)
        config_frame.pack(fill='x', pady=(0, 15))

        tk.Label(config_frame, text="Sorgente Audio:", bg=BG_APP, font=FONT_MAIN).grid(row=0, column=0, sticky='w', pady=5)
        ttk.Radiobutton(config_frame, text='Mantieni Audio Interno', variable=self.mode_var, value='I', command=self.toggle_audio).grid(row=0, column=1, sticky='w', padx=10)
        ttk.Radiobutton(config_frame, text='Sostituisci con Audio Esterno', variable=self.mode_var, value='E', command=self.toggle_audio).grid(row=0, column=2, sticky='w', padx=10)

        tk.Label(config_frame, text="Struttura Video:", bg=BG_APP, font=FONT_MAIN).grid(row=1, column=0, sticky='w', pady=5)
        ttk.Checkbutton(config_frame, text='Includi LEAD tra Hook e Body', variable=self.use_lead_var, command=self.toggle_lead).grid(row=1, column=1, columnspan=2, sticky='w', padx=10)

        self.hooks_widget = FileList(content, '🎬 HOOK (Clip Iniziale)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.hooks_widget.pack(fill='x', pady=8)

        self.leads_widget = FileList(content, '🔗 LEAD (Transizione/Ponte)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.leads_widget.pack(fill='x', pady=8)

        self.bodies_widget = FileList(content, '📹 BODY (Contenuto Principale)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.bodies_widget.pack(fill='x', pady=8)

        self.audios_widget = FileList(content, '🎵 AUDIO ESTERNO (Opzionale)', [('Audio files', '*.mp3 *.wav *.m4a')])
        self.audios_widget.pack(fill='x', pady=8)

        out_frame = tk.Frame(content, bg=BG_APP)
        out_frame.pack(fill='x', pady=15)
        
        tk.Label(out_frame, text='Cartella di salvataggio:', font=FONT_TITLE, bg=BG_APP, fg=COLOR_TEXT).pack(anchor='w')
        path_frame = tk.Frame(out_frame, bg=BG_CARD, highlightbackground='#D1D5DB', highlightthickness=1)
        path_frame.pack(fill='x', pady=5)
        self.out_label = tk.Label(path_frame, text=str(self.output_dir), bg=BG_CARD, fg=COLOR_TEXT, padx=10, pady=8, anchor='w')
        self.out_label.pack(side='left', fill='x', expand=True)
        ttk.Button(path_frame, text='Modifica...', command=self.change_output).pack(side='right', padx=5, pady=5)

        self.run_btn = ttk.Button(content, text='🚀 AVVIA MONTAGGIO VELOCE', style='Accent.TButton', command=self.run)
        self.run_btn.pack(fill='x', pady=10)

        log_container = tk.Frame(content, bg='#1E293B', bd=0, highlightthickness=0)
        log_container.pack(fill='both', expand=True, pady=(10, 0))
        tk.Label(log_container, text="Console Log:", bg='#1E293B', fg='#94A3B8', font=('Segoe UI', 9)).pack(anchor='w', padx=5, pady=2)
        self.txt_log = tk.Text(log_container, height=8, font=FONT_LOG, bg='#0F172A', fg='#38BDF8', bd=0, padx=10, pady=10)
        self.txt_log.pack(fill='both', expand=True)

        self.toggle_audio()
        self.toggle_lead()

    def _on_mousewheel(self, event):
        if getattr(event, 'num', None) == 4: move = -1
        elif getattr(event, 'num', None) == 5: move = 1
        else: move = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(move, 'units')

    def toggle_audio(self):
        self.audios_widget.set_enabled(self.mode_var.get() == 'E')

    def toggle_lead(self):
        use_lead = self.use_lead_var.get()
        self.leads_widget.set_enabled(use_lead)

    def change_output(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir = Path(d)
            self.out_label.config(text=str(self.output_dir))

    def log(self, msg):
        self.txt_log.insert('end', f"> {msg}\n")
        self.txt_log.see('end')
        self.update_idletasks()

    def run(self):
        h = self.hooks_widget.storage
        l = self.leads_widget.storage
        b = self.bodies_widget.storage
        a = self.audios_widget.storage

        use_lead = self.use_lead_var.get()
        use_external_audio = self.mode_var.get() == 'E'

        if not h or not b:
            messagebox.showwarning("Attenzione", "Seleziona almeno un file HOOK e un file BODY.")
            return

        if use_lead and not l:
            messagebox.showwarning("Attenzione", "Hai attivato il LEAD, ma non hai selezionato nessun file per questa sezione.")
            return

        if use_external_audio and not a:
            messagebox.showwarning("Attenzione", "Hai selezionato l'audio esterno, ma non hai caricato nessun file audio.")
            return

        count = 1

        if use_lead:
            combos = product(h, l, b, a) if use_external_audio else product(h, l, b)
        else:
            combos = product(h, b, a) if use_external_audio else product(h, b)

        try:
            self.run_btn.config(state='disabled')
            self.log("Avvio del processo con fix Anti-Blocco (Modalità Veloce)...")
            
            for combo in combos:
                if use_lead:
                    if use_external_audio:
                        hook, lead, body, audio = combo
                        inputs = [hook, lead, body]
                    else:
                        hook, lead, body = combo
                        audio = None
                        inputs = [hook, lead, body]
                else:
                    if use_external_audio:
                        hook, body, audio = combo
                    else:
                        hook, body = combo
                        audio = None
                    inputs = [hook, body]

                name = f"video{count}_hook{h.index(hook) + 1}"
                if use_lead: name += f"_lead{l.index(lead) + 1}"
                name += f"_body{b.index(body) + 1}"
                if audio: name += f"_audio{a.index(audio) + 1}"

                out_path = self.output_dir / f"{name}.mp4"
                self.log(f"Elaborazione: {name}.mp4")

                if use_external_audio:
                    process_concat_external(inputs, audio, out_path)
                else:
                    process_concat_internal(inputs, out_path)

                count += 1

            self.log("✅ PROCESSO COMPLETATO!")
            messagebox.showinfo("Completato", f"Generati {count - 1} video in tempo record!")

        except subprocess.CalledProcessError as e:
            self.log("❌ ERRORE FFMPEG!")
            messagebox.showerror("Errore FFmpeg", str(e))
        except Exception as e:
            self.log("❌ ERRORE IMPREVISTO!")
            messagebox.showerror("Errore imprevisto", str(e))
        finally:
            self.run_btn.config(state='normal')


if __name__ == '__main__':
    app = MontageGUI()
    app.mainloop()
