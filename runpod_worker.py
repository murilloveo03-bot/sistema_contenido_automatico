"""runpod_worker.py - Ensamblador de video interno en RunPod."""

import os
import sys
import json
import subprocess
import boto3
from pathlib import Path

# Configuración AWS (Usará las credenciales del entorno)
AWS_BUCKET = "mis-imagenes-flux-runpod"
s3 = boto3.client('s3')

# Directorios en RunPod
WORKSPACE = Path("/workspace")
DIR_OUTPUT_COMFY = WORKSPACE / "runpod-slim/ComfyUI/output"
DIR_TEMP = WORKSPACE / "temp_ensamblaje"
DIR_TEMP.mkdir(exist_ok=True)

ANCHO, ALTO = 1080, 1920

def descargar_inputs_s3(tema_slug: str):
    """Descarga el Master JSON, MP3 y ASS desde S3."""
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
    return DIR_TEMP / f"MASTER_{tema_slug}.json"

def obtener_filtro_camara(efecto: str, duracion: float) -> str:
    if not efecto or efecto == "static": return ""
    total_f = duracion * 30
    filtros = {
        "pan_right": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/{duracion}*t':'(ih-oh)/2'",
        "pan_left": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)-(iw-ow)/{duracion}*t':'(ih-oh)/2'",
        "tilt_up": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/2':'(ih-oh)-(ih-oh)/{duracion}*t'",
        "tilt_down": f"fps=30,scale={int(ANCHO*1.15)}:{int(ALTO*1.15)},crop={ANCHO}:{ALTO}:'(iw-ow)/2':'(ih-oh)/{duracion}*t'",
        "zoom_in": f"fps=30,zoompan=z='1+0.15*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30",
        "zoom_out": f"fps=30,zoompan=z='1.15-0.15*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30",
        "slow_zoom_in": f"fps=30,zoompan=z='1+0.08*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30",
        "slow_zoom_out": f"fps=30,zoompan=z='1.08-0.08*(on/{total_f})':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:s={ANCHO}x{ALTO}:fps=30"
    }
    return filtros.get(efecto, "")

def ensamblar_video(tema_slug: str, ruta_master: Path):
    with open(ruta_master, "r", encoding="utf-8") as f:
        master_data = json.load(f)
    
    fps = master_data["fps_objetivo"]
    clips = []
    
    for escena in master_data["escenas"]:
        id_escena = escena["id"]
        carpeta_frames = DIR_OUTPUT_COMFY / tema_slug / f"escena_{id_escena:02d}"
        
        imagenes = sorted(list(carpeta_frames.glob("*.png")))
        if len(imagenes) < 2:
            print(f"⚠️ Faltan frames en escena {id_escena}")
            continue

        ruta_secuencia = DIR_TEMP / f"secuencia_{id_escena:02d}.txt"
        with open(ruta_secuencia, "w") as f:
            for i in range(escena["frames_totales"]):
                f.write(f"file '{imagenes[i % 2].as_posix()}'\n")
                f.write(f"duration {1.0/fps:.5f}\n")
            f.write(f"file '{imagenes[(escena['frames_totales'] - 1) % 2].as_posix()}'\n")

        salida_clip = DIR_TEMP / f"clip_{id_escena:02d}.mp4"
        filtro = obtener_filtro_camara(escena["efecto_camara"], escena["frames_totales"]/fps)
        vf_chain = f"{filtro},fps=30,format=yuv420p" if filtro else "fps=30,format=yuv420p"
        
        cmd =["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(ruta_secuencia),
               "-vf", vf_chain, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(salida_clip)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clips.append(salida_clip)

    lista_final = DIR_TEMP / "lista_final.txt"
    with open(lista_final, "w") as f:
        for c in clips: f.write(f"file '{c.as_posix()}'\n")

    video_final = DIR_TEMP / f"{tema_slug}_FINAL.mp4"
    audio_maestro = DIR_TEMP / f"{tema_slug}_MAESTRO.mp3"
    ruta_ass = str(DIR_TEMP / f"{tema_slug}_MAESTRO.ass").replace('\\', '/').replace(':', '\\:')
    
    cmd_final =[
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista_final),
        "-i", str(audio_maestro), "-vf", f"ass='{ruta_ass}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", 
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(video_final)
    ]
    print(f"🎬 Renderizando video final con FFmpeg...")
    subprocess.run(cmd_final)
    
    return video_final

def main():
    if len(sys.argv) < 2:
        print("Uso: python runpod_worker.py <tema_slug>")
        sys.exit(1)
        
    tema_slug = sys.argv[1]
    print(f"\n=== INICIANDO WORKER PARA: {tema_slug} ===")
    
    ruta_master = descargar_inputs_s3(tema_slug)
    video_final = ensamblar_video(tema_slug, ruta_master)
    
    print(f"☁️ Subiendo {video_final.name} a S3...")
    s3.upload_file(str(video_final), AWS_BUCKET, f"outputs/{video_final.name}")
    
    # Limpieza opcional para ahorrar espacio en el Pod
    os.system(f"rm -rf {DIR_OUTPUT_COMFY}/{tema_slug}")
    print("✅ ¡Proceso completado con éxito!")

if __name__ == "__main__":
    main()
