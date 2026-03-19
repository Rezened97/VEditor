import os
import re
import subprocess
import tempfile
import zipfile
from io import BytesIO
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


def process_concat_internal(inputs, output: Path):
    temp_ts_files = []

    for i, p in enumerate(inputs):
        ts = output.with_name(f"{output.stem}_temp_{i}.ts")
        cmd = [
            ffmpeg_bin, "-y", "-i", str(p),
            "-c:v", "copy",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-f", "mpegts", str(ts)
        ]
        subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
        temp_ts_files.append(ts)

    list_file = output.with_name(f"{output.stem}_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for ts in temp_ts_files:
            f.write(f"file '{ts.resolve().as_posix()}'\n")

    cmd_concat = [
        ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output)
    ]
    subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

    if list_file.exists():
        list_file.unlink()
    for ts in temp_ts_files:
        if ts.exists():
            ts.unlink()


def process_concat_external(inputs, audio: Path, output: Path):
    temp_ts_files = []

    for i, p in enumerate(inputs):
        ts = output.with_name(f"{output.stem}_temp_{i}.ts")
        cmd = [
            ffmpeg_bin, "-y", "-i", str(p),
            "-c:v", "copy",
            "-an",
            "-f", "mpegts", str(ts)
        ]
        subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
        temp_ts_files.append(ts)

    list_file = output.with_name(f"{output.stem}_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for ts in temp_ts_files:
            f.write(f"file '{ts.resolve().as_posix()}'\n")

    temp_vid = output.with_name(f"{output.stem}_v.mp4")
    cmd_concat = [
        ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(temp_vid)
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
        "-shortest",
        str(output)
    ], check=True, startupinfo=STARTUPINFO)

    if list_file.exists():
        list_file.unlink()
    if temp_vid.exists():
        temp_vid.unlink()
    if adj_audio != audio and adj_audio.exists():
        try:
            adj_audio.unlink()
        except OSError:
            pass
    for ts in temp_ts_files:
        if ts.exists():
            ts.unlink()


def save_uploaded_files(uploaded_files, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for up in uploaded_files:
        safe_name = Path(up.name).name
        out_path = target_dir / safe_name
        out_path.write_bytes(up.getbuffer())
        saved.append(out_path)
    return saved


def zip_folder_to_bytes(folder: Path) -> bytes:
    mem_zip = BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in folder.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(folder))
    mem_zip.seek(0)
    return mem_zip.getvalue()


st.set_page_config(page_title="Automazione Montaggio Video", layout="wide")

if "logs" not in st.session_state:
    st.session_state.logs = []

st.title("🎬 Generatore Video Multiplo")
st.caption("Carica i file trascinandoli dentro i box qui sotto oppure cliccando su Browse files")

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

run_btn = st.button("🚀 AVVIA MONTAGGIO", type="primary", use_container_width=True)

if run_btn:
    st.session_state.logs = []

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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_dir = tmp_dir / "inputs"
            output_dir = tmp_dir / "video_finali"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)

            hooks = save_uploaded_files(hooks_up, input_dir / "hooks")
            bodies = save_uploaded_files(bodies_up, input_dir / "bodies")
            leads = save_uploaded_files(leads_up, input_dir / "leads") if use_lead else []
            audios = save_uploaded_files(audios_up, input_dir / "audios") if audio_mode == "E" else []

            if use_lead:
                combos = list(product(hooks, leads, bodies, audios)) if audio_mode == "E" else list(product(hooks, leads, bodies))
            else:
                combos = list(product(hooks, bodies, audios)) if audio_mode == "E" else list(product(hooks, bodies))

            total = len(combos)
            count = 1

            log("Avvio del processo con fix Anti-Blocco (Modalità Veloce)...")

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

                out_path = output_dir / f"{name}.mp4"
                status.info(f"Elaborazione: {name}.mp4")
                log(f"Elaborazione: {name}.mp4")

                if audio_mode == "E":
                    process_concat_external(inputs, audio, out_path)
                else:
                    process_concat_internal(inputs, out_path)

                progress.progress(idx / total)
                count += 1

            log("✅ PROCESSO COMPLETATO!")
            status.success(f"Generati {count - 1} video")

            generated_files = sorted(output_dir.glob("*.mp4"))

            if not generated_files:
                st.warning("Nessun file video generato.")
            elif len(generated_files) == 1:
                single_file = generated_files[0]
                st.success(f"Video pronto: {single_file.name}")
                st.video(str(single_file))
                st.download_button(
                    "⬇️ Scarica video",
                    data=single_file.read_bytes(),
                    file_name=single_file.name,
                    mime="video/mp4",
                    use_container_width=True,
                )
            else:
                zip_bytes = zip_folder_to_bytes(output_dir)
                st.success(f"Generati {len(generated_files)} video")
                st.download_button(
                    "⬇️ Scarica tutti i video in ZIP",
                    data=zip_bytes,
                    file_name="video_finali.zip",
                    mime="application/zip",
                    use_container_width=True,
                )

    except subprocess.CalledProcessError as e:
        st.error(f"Errore FFmpeg: {e}")
        log("❌ ERRORE FFMPEG!")
    except Exception as e:
        st.error(f"Errore imprevisto: {e}")
        log(f"❌ ERRORE IMPREVISTO: {e}")

if st.session_state.logs:
    st.subheader("Console Log")
    st.code("\n".join(st.session_state.logs), language="text")
