import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from itertools import product

import streamlit as st

# Use imageio-ffmpeg bundled ffmpeg binary
try:
    from imageio_ffmpeg import get_ffmpeg_exe
    ffmpeg_bin = get_ffmpeg_exe()
except ImportError:
    st.error("Installa imageio-ffmpeg nel requirements.txt")
    st.stop()

# Hide ffmpeg console on Windows
STARTUPINFO = None
if os.name == "nt":
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    STARTUPINFO = si


def log(msg: str):
    st.session_state.logs.append(f"> {msg}")


def safe_output_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def get_media_duration(path: Path) -> float:
    cmd = [ffmpeg_bin, "-i", str(path)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO,
    )
    _, err = proc.communicate()
    text = err.decode(errors="ignore")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def adjust_audio_speed(audio: Path, target_duration: float) -> Path:
    orig = get_media_duration(audio)
    if orig <= 0 or target_duration <= 0:
        return audio

    speed = orig / target_duration
    filters = []
    factor = speed

    while factor < 0.5:
        filters.append("atempo=0.5")
        factor /= 0.5
    while factor > 2.0:
        filters.append("atempo=2.0")
        factor /= 2.0

    filters.append(f"atempo={factor}")
    filt = ",".join(filters)

    out_file = audio.with_name(f"{audio.stem}_adj{audio.suffix}")
    cmd = [ffmpeg_bin, "-y", "-i", str(audio), "-filter:a", filt, str(out_file)]
    subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
    return out_file


def normalize_video(input_path: Path, output_path: Path, keep_audio: bool = True):
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(input_path),
        "-vf", "scale='min(1280,iw)':-2,fps=25",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "27",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    if keep_audio:
        cmd += [
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "128k",
        ]
    else:
        cmd += ["-an"]

    cmd.append(str(output_path))
    subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)


def preprocess_files(files, out_dir: Path, keep_audio: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    processed = []

    for idx, src in enumerate(files, start=1):
        out_path = out_dir / f"{idx:03d}_{safe_output_name(src.stem)}.mp4"
        normalize_video(src, out_path, keep_audio=keep_audio)
        processed.append(out_path)

    return processed


def process_concat_internal(inputs, output: Path):
    list_file = output.with_name(f"{output.stem}_list.txt")

    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in inputs:
                f.write(f"file '{p.resolve().as_posix()}'\n")

        cmd_concat = [
            ffmpeg_bin, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output),
        ]
        subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

    finally:
        if list_file.exists():
            list_file.unlink()


def process_concat_external(inputs, audio: Path, output: Path):
    list_file = output.with_name(f"{output.stem}_list.txt")
    temp_vid = output.with_name(f"{output.stem}_v.mp4")

    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in inputs:
                f.write(f"file '{p.resolve().as_posix()}'\n")

        cmd_concat = [
            ffmpeg_bin, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(temp_vid),
        ]
        subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

        total_dur = sum(get_media_duration(p) for p in inputs)
        adj_audio = adjust_audio_speed(audio, total_dur)

        subprocess.run([
            ffmpeg_bin, "-y",
            "-i", str(temp_vid),
            "-i", str(adj_audio),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            str(output)
        ], check=True, startupinfo=STARTUPINFO)

    finally:
        if list_file.exists():
            list_file.unlink()
        if temp_vid.exists():
            temp_vid.unlink()

        adj_audio_candidate = audio.with_name(f"{audio.stem}_adj{audio.suffix}")
        if adj_audio_candidate.exists() and adj_audio_candidate != audio:
            try:
                adj_audio_candidate.unlink()
            except OSError:
                pass


def save_uploaded_files(uploaded_files, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for up in uploaded_files:
        safe_name = Path(up.name).name
        out_path = target_dir / safe_name
        out_path.write_bytes(up.getbuffer())
        saved.append(out_path)
    return saved


def zip_folder_to_file(folder: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(folder))


def init_state():
    if "logs" not in st.session_state:
        st.session_state.logs = []
    if "job_dir" not in st.session_state:
        st.session_state.job_dir = None
    if "zip_path" not in st.session_state:
        st.session_state.zip_path = None
    if "single_file_path" not in st.session_state:
        st.session_state.single_file_path = None
    if "generated_count" not in st.session_state:
        st.session_state.generated_count = 0
    if "job_ready" not in st.session_state:
        st.session_state.job_ready = False


def reset_previous_job():
    old_job_dir = st.session_state.get("job_dir")
    if old_job_dir and Path(old_job_dir).exists():
        try:
            shutil.rmtree(old_job_dir, ignore_errors=True)
        except Exception:
            pass

    st.session_state.job_dir = None
    st.session_state.zip_path = None
    st.session_state.single_file_path = None
    st.session_state.generated_count = 0
    st.session_state.job_ready = False


init_state()

st.set_page_config(page_title="Automazione Montaggio Video", layout="wide")
st.title("🎬 Generatore Video Multiplo")
st.caption("Carica i file trascinandoli nei box oppure cliccando su Browse files")

with st.container(border=True):
    col1, col2 = st.columns(2)

    with col1:
        audio_mode = st.radio(
            "Sorgente Audio",
            options=["I", "E"],
            format_func=lambda x: "Mantieni Audio Interno" if x == "I" else "Sostituisci con Audio Esterno",
            horizontal=True
        )

    with col2:
        use_lead = st.checkbox("Includi LEAD tra Hook e Body", value=True)

st.subheader("Caricamento file")

col_h, col_l = st.columns(2)
with col_h:
    hooks_up = st.file_uploader(
        "🎬 HOOK (clip iniziale)",
        type=["mp4", "mov", "avi", "mkv"],
        accept_multiple_files=True,
        key="hooks"
    )

with col_l:
    leads_up = st.file_uploader(
        "🔗 LEAD (transizione/ponte)",
        type=["mp4", "mov", "avi", "mkv"],
        accept_multiple_files=True,
        disabled=not use_lead,
        key="leads"
    )

col_b, col_a = st.columns(2)
with col_b:
    bodies_up = st.file_uploader(
        "📹 BODY (contenuto principale)",
        type=["mp4", "mov", "avi", "mkv"],
        accept_multiple_files=True,
        key="bodies"
    )

with col_a:
    audios_up = st.file_uploader(
        "🎵 AUDIO ESTERNO",
        type=["mp3", "wav", "m4a"],
        accept_multiple_files=True,
        disabled=(audio_mode != "E"),
        key="audios"
    )

with st.expander("Anteprima file selezionati", expanded=False):
    st.write("**HOOK**", [f.name for f in hooks_up] if hooks_up else [])
    st.write("**LEAD**", [f.name for f in leads_up] if leads_up else [])
    st.write("**BODY**", [f.name for f in bodies_up] if bodies_up else [])
    st.write("**AUDIO**", [f.name for f in audios_up] if audios_up else [])

if hooks_up and bodies_up:
    if use_lead:
        combinations = len(hooks_up) * len(leads_up or []) * len(bodies_up) * (
            len(audios_up) if audio_mode == "E" and audios_up else 1
        )
    else:
        combinations = len(hooks_up) * len(bodies_up) * (
            len(audios_up) if audio_mode == "E" and audios_up else 1
        )
    st.info(f"Combinazioni previste: {combinations}")

run_btn = st.button("🚀 AVVIA MONTAGGIO", type="primary", width="stretch")

if run_btn:
    st.session_state.logs = []
    reset_previous_job()

    if not hooks_up or not bodies_up:
        st.error("Seleziona almeno un file HOOK e un file BODY.")
        st.stop()

    if use_lead and not leads_up:
        st.error("Hai attivato il LEAD, ma non hai selezionato nessun file per questa sezione.")
        st.stop()

    if audio_mode == "E" and not audios_up:
        st.error("Hai selezionato l'audio esterno, ma non hai caricato nessun file audio.")
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    try:
        job_dir = Path(tempfile.mkdtemp(prefix="vidgen_"))
        input_dir = job_dir / "inputs"
        output_dir = job_dir / "video_finali"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        st.session_state.job_dir = str(job_dir)

        raw_hooks = save_uploaded_files(hooks_up, input_dir / "hooks_raw")
        raw_bodies = save_uploaded_files(bodies_up, input_dir / "bodies_raw")
        raw_leads = save_uploaded_files(leads_up, input_dir / "leads_raw") if use_lead else []
        audios = save_uploaded_files(audios_up, input_dir / "audios") if audio_mode == "E" else []

        status.info("Normalizzazione HOOK...")
        log("Normalizzazione HOOK...")
        hooks = preprocess_files(raw_hooks, input_dir / "hooks_norm", keep_audio=(audio_mode == "I"))
        progress.progress(0.1)

        if use_lead:
            status.info("Normalizzazione LEAD...")
            log("Normalizzazione LEAD...")
            leads = preprocess_files(raw_leads, input_dir / "leads_norm", keep_audio=(audio_mode == "I"))
        else:
            leads = []
        progress.progress(0.25)

        status.info("Normalizzazione BODY...")
        log("Normalizzazione BODY...")
        bodies = preprocess_files(raw_bodies, input_dir / "bodies_norm", keep_audio=(audio_mode == "I"))
        progress.progress(0.4)

        if use_lead:
            combos = list(product(hooks, leads, bodies, audios)) if audio_mode == "E" else list(product(hooks, leads, bodies))
        else:
            combos = list(product(hooks, bodies, audios)) if audio_mode == "E" else list(product(hooks, bodies))

        total = len(combos)
        count = 1

        log("Avvio del processo di montaggio (modalità ottimizzata cloud)...")

        start_progress = 0.4
        remaining_progress = 0.5

        for idx, combo in enumerate(combos, start=1):
            if use_lead:
                if audio_mode == "E":
                    hook, lead, body, audio = combo
                    inputs = [hook, lead, body]
                else:
                    hook, lead, body = combo
                    audio = None
                    inputs = [hook, lead, body]
            else:
                if audio_mode == "E":
                    hook, body, audio = combo
                else:
                    hook, body = combo
                    audio = None
                inputs = [hook, body]

            name = f"video{count}_hook{hooks.index(hook) + 1}"
            if use_lead:
                name += f"_lead{leads.index(lead) + 1}"
            name += f"_body{bodies.index(body) + 1}"
            if audio:
                name += f"_audio{audios.index(audio) + 1}"

            name = safe_output_name(name)
            out_path = output_dir / f"{name}.mp4"

            status.info(f"Elaborazione {idx}/{total}: {name}.mp4")
            log(f"Elaborazione: {name}.mp4")

            if audio_mode == "E":
                process_concat_external(inputs, audio, out_path)
            else:
                process_concat_internal(inputs, out_path)

            current_progress = start_progress + (idx / total) * remaining_progress
            progress.progress(min(current_progress, 0.9))
            count += 1

        generated_files = sorted(output_dir.glob("*.mp4"))
        st.session_state.generated_count = len(generated_files)

        if not generated_files:
            st.warning("Nessun file video generato.")
            st.stop()

        if len(generated_files) == 1:
            st.session_state.single_file_path = str(generated_files[0])
            st.session_state.zip_path = None
        else:
            log("Creazione ZIP finale...")
            status.info("Creazione ZIP finale...")
            zip_path = job_dir / "video_finali.zip"
            zip_folder_to_file(output_dir, zip_path)
            st.session_state.zip_path = str(zip_path)
            st.session_state.single_file_path = None

        st.session_state.job_ready = True
        progress.progress(1.0)

        log("✅ PROCESSO COMPLETATO!")
        status.success(f"Generati {st.session_state.generated_count} video")

    except subprocess.CalledProcessError as e:
        st.error(f"Errore FFmpeg: {e}")
        log(f"❌ ERRORE FFMPEG: {e}")
    except Exception as e:
        st.error(f"Errore imprevisto: {e}")
        log(f"❌ ERRORE IMPREVISTO: {e}")

if st.session_state.job_ready:
    st.success(f"Output pronto. File generati: {st.session_state.generated_count}")

    single_file_path = st.session_state.get("single_file_path")
    zip_path = st.session_state.get("zip_path")

    if single_file_path and Path(single_file_path).exists():
        with open(single_file_path, "rb") as f:
            st.download_button(
                "⬇️ Scarica video",
                data=f.read(),
                file_name=Path(single_file_path).name,
                mime="video/mp4",
                on_click="ignore",
                width="stretch",
            )
    elif zip_path and Path(zip_path).exists():
        with open(zip_path, "rb") as f:
            st.download_button(
                "⬇️ Scarica tutti i video in ZIP",
                data=f.read(),
                file_name="video_finali.zip",
                mime="application/zip",
                on_click="ignore",
                width="stretch",
            )
    else:
        st.error("Il file finale non è più disponibile nella sessione corrente. Rigenera il batch.")

if st.session_state.logs:
    st.subheader("Console Log")
    st.code("\n".join(st.session_state.logs), language="text")
