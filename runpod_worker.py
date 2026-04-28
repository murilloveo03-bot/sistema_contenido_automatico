import os
import sys
import json
import subprocess
import boto3
from pathlib import Path

# Configuración AWS
AWS_BUCKET = "mis-imagenes-flux-runpod"
s3 = boto3.client('s3')

# Directorios en RunPod
WORKSPACE = Path("/workspace")
DIR_OUTPUT_COMFY = WORKSPACE / "runpod-slim/ComfyUI/output"
DIR_TEMP = WORKSPACE / "temp_ensamblaje"
DIR_TEMP.mkdir(exist_ok=True)

#ANCHO, ALTO = 1080, 1920
ANCHO, ALTO = 1440, 2560
VOLUMEN_FONDO = os.environ.get("VOLUMEN_FONDO", "1.0")

def descargar_inputs_s3(tema_slug: str):
    archivos =[
        f"inputs/MASTER_{tema_slug}.json",
        f"inputs/{tema_slug}_MAESTRO.mp3",
        f"inputs/{tema_slug}_MAESTRO.ass"
    ]
    for key in archivos:
        nombre_archivo = key.split('/')[-1]
        ruta_local = DIR_TEMP / nombre_archivo
        print(f"📥 Descargando {nombre_archivo} de S3...")
        s3.download_file(AWS_BUCKET, key, str(ruta_local))
        
    try:
        s3.download_file(AWS_BUCKET, "inputs/background_audio.mp3", str(DIR_TEMP / "background_audio.mp3"))
        print("📥 Audio de fondo descargado.")
    except Exception:
        pass

    return DIR_TEMP / f"MASTER_{tema_slug}.json"

def obtener_filtro_camara(efecto: str, duracion: float) -> str:
    # EL BLINDAJE: Esto fuerza a que todo clip cuadre perfecto, tenga efecto o no.
    base_fix = f"scale={ANCHO}:{ALTO},setsar=1"
    
    if not efecto or efecto == "static": 
        return base_fix
    
    dur = max(float(duracion), 0.1)
    total_f = dur * 30
    
    filtros = {
        "pan_right": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/{dur}*t':'(ih-oh)/2',{base_fix}",
        "pan_left": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)-(iw-ow)/{dur}*t':'(ih-oh)/2',{base_fix}",
        "tilt_up": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/2':'(ih-oh)-(ih-oh)/{dur}*t',{base_fix}",
        "tilt_down": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/2':'(ih-oh)/{dur}*t',{base_fix}",
        "zoom_in": f"fps=30,zoompan=z='1+0.15*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30,{base_fix}",
        "zoom_out": f"fps=30,zoompan=z='1.15-0.15*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30,{base_fix}",
        "slow_zoom_in": f"fps=30,zoompan=z='1+0.08*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30,{base_fix}",
        "slow_zoom_out": f"fps=30,zoompan=z='1.08-0.08*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30,{base_fix}"
    }
    return filtros.get(efecto, base_fix)

def ensamblar_video(tema_slug: str, ruta_master: Path):
    with open(ruta_master, "r", encoding="utf-8") as f:
        master_data = json.load(f)
    
    fps = master_data.get("fps_objetivo", 6)
    clips = []
    
    for escena in master_data["escenas"]:
        id_escena = escena["id"]
        carpeta_frames = DIR_OUTPUT_COMFY / tema_slug / f"escena_{id_escena:02d}"
        
        imagenes = sorted(list(carpeta_frames.glob("*.png")))
        if len(imagenes) < 2:
            continue

        ruta_secuencia = DIR_TEMP / f"secuencia_{id_escena:02d}.txt"
        with open(ruta_secuencia, "w") as f:
            for i in range(escena["frames_totales"]):
                f.write(f"file '{imagenes[i % 2].as_posix()}'\n")
                f.write(f"duration {1.0/fps:.5f}\n")
            f.write(f"file '{imagenes[(escena['frames_totales'] - 1) % 2].as_posix()}'\n")

        salida_clip = DIR_TEMP / f"clip_{id_escena:02d}.mp4"
        filtro = obtener_filtro_camara(escena["efecto_camara"], escena["frames_totales"]/fps)
        
        # El filtro ya está blindado, solo añadimos fps de salida y formato de píxel
        vf_chain = f"{filtro},fps=30,format=yuv420p"
        
        cmd =[
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(ruta_secuencia),
            "-vf", vf_chain, 
            "-c:v", "libx264", "-pix_fmt", "yuv420p", 
            "-video_track_timescale", "90000", "-r", "30",
            "-threads", "4",
            str(salida_clip)
        ]
        subprocess.run(cmd, stderr=subprocess.STDOUT)
        
        # Solo añadimos el clip a la lista final si realmente se generó y no está vacío
        if salida_clip.exists() and salida_clip.stat().st_size > 1000:
            clips.append(salida_clip)

    lista_final = DIR_TEMP / "lista_final.txt"
    with open(lista_final, "w") as f:
        for c in clips: f.write(f"file '{c.as_posix()}'\n")

    video_final = DIR_TEMP / f"{tema_slug}_FINAL.mp4"
    audio_maestro = DIR_TEMP / f"{tema_slug}_MAESTRO.mp3"
    ruta_ass = str(DIR_TEMP / f"{tema_slug}_MAESTRO.ass").replace('\\', '/').replace(':', '\\:')
    ruta_fondo = DIR_TEMP / "background_audio.mp3"
    
    filtro_voz = "loudnorm=I=-14:LRA=11:TP=-1.5"
    
    # === LA MAGIA ANTI-COMPRESIÓN ===
    # 1. ass: Aplica tus subtítulos
    # 2. eq: Levanta el brillo general un 1.5% para matar el negro #000000 puro
    # 3. noise: Agrega ruido/grano temporal suave (alls=2) para engañar al algoritmo
    vf_final = f"ass='{ruta_ass}',eq=brightness=0.015,noise=alls=2:allf=t"
    
    print(f"🎬 Renderizando video final con FFmpeg (Calidad Anti-Compresión)...")
    
    if ruta_fondo.exists():
        cmd_final =[
            "ffmpeg", "-y", 
            "-f", "concat", "-safe", "0", "-i", str(lista_final), 
            "-i", str(audio_maestro),                             
            "-stream_loop", "-1", "-i", str(ruta_fondo),               
            "-filter_complex", 
            f"[1:a]{filtro_voz}[voice];[2:a]volume={VOLUMEN_FONDO}[bg];[voice][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
            "-vf", vf_final,
            "-map", "0:v", "-map", "[aout]", # <-- Aquí sí existe [aout]
            "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-maxrate", "40M", "-bufsize", "80M", "-r", "30", "-pix_fmt", "yuv420p",
            "-threads", "6", 
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(video_final)
        ]
    else:
        cmd_final =[
            "ffmpeg", "-y", 
            "-f", "concat", "-safe", "0", "-i", str(lista_final), 
            "-i", str(audio_maestro),
            "-af", filtro_voz,
            "-vf", vf_final, 
            "-map", "0:v", "-map", "1:a", # <-- ¡CORRECCIÓN! Mapeamos el audio original procesado
            "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-maxrate", "40M", "-bufsize", "80M", "-r", "30", "-pix_fmt", "yuv420p",
            "-threads", "6", 
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(video_final)
        ]
        
    subprocess.run(cmd_final)
    return video_final

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
        
    tema_slug = sys.argv[1]
    print(f"\n=== INICIANDO WORKER PARA: {tema_slug} ===")
    
    ruta_master = descargar_inputs_s3(tema_slug)
    video_final = ensamblar_video(tema_slug, ruta_master)
    
    if video_final.exists() and video_final.stat().st_size > 0:
        print(f"☁️ Subiendo {video_final.name} a S3...")
        s3.upload_file(str(video_final), AWS_BUCKET, f"outputs/{video_final.name}")
        print("✅ ¡Proceso completado con éxito!")
    else:
        print("❌ Error fatal: El archivo final no se generó correctamente.")
        
    os.system(f"rm -rf {DIR_OUTPUT_COMFY}/{tema_slug}")

if __name__ == "__main__":
    main()