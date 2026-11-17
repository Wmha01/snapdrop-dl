import os
import requests
import urllib.parse
import re
import tempfile
import time
import json
import uuid
from flask import Flask, render_template, request, Response, stream_with_context, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

# ==========================================
# 1. CONFIGURACIÓN INICIAL Y CACHÉ
# ==========================================
directorio_base = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, 
            template_folder=os.path.join(directorio_base, 'templates'),
            static_folder=os.path.join(directorio_base, 'static'))

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["300 per day", "60 per hour"],
    storage_uri="memory://",
)

progreso_usuarios = {}
cache_enlaces = {}  # Memoria caché: {clave: (timestamp, datos)}
TIEMPO_CACHE = 600  # 10 minutos
LIMITE_DURACION = 1200  # 20 minutos máximos

USER_AGENT_PRO = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

def limpiar_y_expandir_url(url_cruda):
    """Expande links cortos (vt.tiktok, vm.tiktok, bit.ly) y limpia basura de rastreo."""
    if not url_cruda:
        return ""
    url = url_cruda.strip()
    
    # Si es un enlace corto de TikTok, seguimos la redirección primero
    if any(short in url.lower() for short in ['vt.tiktok.com', 'vm.tiktok.com', 'tiktok.com/t/']):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=8, headers={'User-Agent': USER_AGENT_PRO})
            url = resp.url
        except Exception:
            pass

    # Limpieza específica para TikTok
    if 'tiktok.com' in url.lower():
        url = url.split('?')[0]
        if '/photo/' in url:
            url = url.replace('/photo/', '/video/')
            
    return url

@app.route('/')
def index():
    return render_template('index.html')

# ==========================================
# 2. CANAL SSE (BARRA DE PROGRESO)
# ==========================================
@app.route('/progreso')
def progreso():
    usuario_ip = get_remote_address()
    def generar():
        intentos = 0
        while intentos < 120:  # Máximo 60 segundos escuchando
            p = progreso_usuarios.get(usuario_ip, 0)
            yield f"data: {json.dumps({'porcentaje': p})}\n\n"
            if p >= 100: 
                break
            time.sleep(0.5)
            intentos += 1
    return Response(generar(), mimetype='text/event-stream')

# ==========================================
# 3. PROCESAMIENTO E INTELIGENCIA
# ==========================================
@app.route('/procesar', methods=['POST'])
@limiter.limit("10 per minute")
def procesar_enlace():
    url_original = request.form.get('enlace', '')
    calidad = request.form.get('calidad', 'alta')
    usuario_ip = get_remote_address()
    
    progreso_usuarios[usuario_ip] = 15 
    url = limpiar_y_expandir_url(url_original)
    
    # Verificamos si ya está en Caché
    clave_cache = f"{url}_{calidad}"
    ahora = time.time()
    if clave_cache in cache_enlaces:
        tiempo_guardado, datos_guardados = cache_enlaces[clave_cache]
        if ahora - tiempo_guardado < TIEMPO_CACHE:
            progreso_usuarios[usuario_ip] = 100
            return jsonify(datos_guardados)

    formato_extraccion = 'best'
    if calidad == 'audio':
        formato_extraccion = 'bestaudio/best'
    elif calidad == 'media':
        formato_extraccion = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]/best'
    elif calidad == 'baja':
        formato_extraccion = 'bestvideo[height<=360]+bestaudio/best[height<=360]/best[height<=360]/best'

    opciones = {
        'format': formato_extraccion,
        'quiet': True,
        'skip_download': True,
        'nocheckcertificate': True,
        'socket_timeout': 20,
        'retries': 3,
        'noplaylist': True,
        'playlist_items': '1',
        'user_agent': USER_AGENT_PRO,
        'referer': 'https://www.tiktok.com/' if 'tiktok' in url.lower() else url,
        'http_headers': {
            'User-Agent': USER_AGENT_PRO,
            'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'
        }
    }

    try:
        progreso_usuarios[usuario_ip] = 40
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']: 
                info = info['entries'][0]

            es_imagen = False
            if info.get('duration') is None and info.get('vcodec') == 'none':
                es_imagen = True

            duracion = info.get('duration') or 0
            if duracion > LIMITE_DURACION:
                progreso_usuarios[usuario_ip] = 100
                return jsonify({'status': 'error', 'message': 'El medio supera los 20 minutos permitidos.'})

            d_url = info.get('url')
            url_minuscula = url.lower()
            titulo_crudo = info.get('title') or 'SnapDrop_Media'
            miniatura = info.get('thumbnail') or ''
            autor = info.get('uploader') or info.get('creator') or 'Red Social'
            
            titulo_limpio = re.sub(r'[^\w\s-]', '', titulo_crudo).strip().replace(' ', '_')
            titulo_limpio = titulo_limpio[:50] if titulo_limpio else 'SnapDrop_Archivo'

            if es_imagen or calidad == 'imagen':
                modo = 'directo'
                ext = 'jpg'
                url_a_empaquetar = miniatura or d_url or url
            else:
                ext = 'mp3' if calidad == 'audio' else 'mp4'
                if not d_url or '.m3u8' in d_url or 'tiktok' in url_minuscula or calidad == 'audio':
                    modo = 'servidor'
                    url_a_empaquetar = url
                else:
                    modo = 'directo'
                    url_a_empaquetar = d_url
            
            safe_url = urllib.parse.quote(url_a_empaquetar, safe='')
            safe_title = urllib.parse.quote(titulo_limpio, safe='')
            
            url_final_pdp = f"/descargar?url={safe_url}&titulo={safe_title}&ext={ext}&modo={modo}&calidad={calidad}"
            
            respuesta = {
                'status': 'success',
                'url_descarga': url_final_pdp,
                'miniatura': miniatura,
                'titulo': titulo_crudo[:65] + ("..." if len(titulo_crudo) > 65 else ""),
                'autor': autor,
                'duracion': duracion,
                'ext': ext,
                'preview_url': d_url if (d_url and modo == 'directo' and ext == 'mp4') else ''
            }
            
            cache_enlaces[clave_cache] = (ahora, respuesta)
            progreso_usuarios[usuario_ip] = 100 
            return jsonify(respuesta)

    except Exception as e:
        print(f"Error procesando enlace: {e}")
        progreso_usuarios[usuario_ip] = 100
        return jsonify({'status': 'error', 'message': 'Enlace privado, temporalmente bloqueado o plataforma no soportada.'})

# ==========================================
# 4. MOTOR DE DESCARGA FINAL (BLINDADO)
# ==========================================
@app.route('/descargar')
def descargar_archivo():
    video_url = limpiar_y_expandir_url(urllib.parse.unquote(request.args.get('url', '')))
    titulo = request.args.get('titulo', 'SnapDrop')
    ext = request.args.get('ext', 'mp4')
    modo = request.args.get('modo', 'directo')
    calidad = request.args.get('calidad', 'alta')
    usuario_ip = get_remote_address()

    if modo == 'directo':
        r = requests.get(video_url, stream=True, headers={'User-Agent': USER_AGENT_PRO})
        return Response(stream_with_context(r.iter_content(chunk_size=8192)), headers={
            "Content-Disposition": f'attachment; filename="{titulo}.{ext}"',
            "Content-Type": "application/octet-stream"
        })
        
    else:
        # Usamos un ID único para que si 2 personas descargan al mismo tiempo no se mezclen sus archivos
        id_unico = uuid.uuid4().hex[:8]
        temp_path_base = os.path.join(tempfile.gettempdir(), f"{titulo}_{id_unico}")
        expected_file = f"{temp_path_base}.{ext}"
        
        def hook(d):
            if d['status'] == 'downloading':
                p = d.get('_percent_str', '0%').replace('%','')
                try: progreso_usuarios[usuario_ip] = float(p)
                except: pass

        formato_bajada = 'bestvideo+bestaudio/best'
        if calidad == 'audio':
            formato_bajada = 'bestaudio/best'
        elif calidad == 'media':
            formato_bajada = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=720]/best'
        elif calidad == 'baja':
            formato_bajada = 'bestvideo[height<=360]+bestaudio/best[height<=360]/best[height<=360]/best'

        opciones_bajada = {
            'outtmpl': f"{temp_path_base}.%(ext)s", 
            'format': formato_bajada, 
            'quiet': True,
            'nocheckcertificate': True,
            'socket_timeout': 25,
            'retries': 3,
            'user_agent': USER_AGENT_PRO,
            'referer': 'https://www.tiktok.com/' if 'tiktok' in video_url.lower() else video_url,
            'progress_hooks': [hook]
        }
        
        if ext == 'mp3':
            # Incrustamos metadatos y carátula automáticamente en el MP3
            opciones_bajada['writethumbnail'] = True
            opciones_bajada['postprocessors'] = [
                {
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                },
                {'key': 'EmbedThumbnail'},
                {'key': 'FFmpegMetadata'}
            ]
        else:
            opciones_bajada['merge_output_format'] = 'mp4'
            opciones_bajada['postprocessors'] = [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4'
            }]
        
        with yt_dlp.YoutubeDL(opciones_bajada) as ydl:
            ydl.download([video_url])
        
        def stream_and_remove():
            try:
                with open(expected_file, 'rb') as f:
                    yield from f
            finally:
                # Limpiamos el archivo y cualquier miniatura temporal residual
                for archivo in os.listdir(tempfile.gettempdir()):
                    if archivo.startswith(f"{titulo}_{id_unico}"):
                        try:
                            os.remove(os.path.join(tempfile.gettempdir(), archivo))
                        except OSError:
                            pass
            
        return Response(stream_with_context(stream_and_remove()), headers={
            "Content-Disposition": f'attachment; filename="{titulo}.{ext}"',
            "Content-Type": "application/octet-stream"
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
