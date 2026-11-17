import os
import re
import time
import uuid
import tempfile
import urllib.parse
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, Response, stream_with_context, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

directorio_base = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, 
            template_folder=os.path.join(directorio_base, 'templates'),
            static_folder=os.path.join(directorio_base, 'static'))

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["400 per day", "80 per hour"],
    storage_uri="memory://",
)

cache_analisis = {}
TIEMPO_CACHE = 600
LIMITE_DURACION = 1200

USER_AGENT_PRO = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'

def limpiar_y_expandir_url(url_cruda):
    if not url_cruda:
        return ""
    url = url_cruda.strip()
    if any(short in url.lower() for short in ['vt.tiktok.com', 'vm.tiktok.com', 'tiktok.com/t/', 'pin.it']):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=8, headers={'User-Agent': USER_AGENT_PRO})
            url = resp.url
        except Exception:
            pass

    if 'tiktok.com' in url.lower():
        url = url.split('?')[0]
    return url

def extraer_respaldo_imagen_o_ig(url):
    """Motor secundario cuando yt-dlp falla (ej. Fotos de Instagram, Pinterest, X)."""
    headers = {
        'User-Agent': USER_AGENT_PRO,
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'
    }

    # 1. Intento especializado para Instagram (vía vista Embed pública)
    if 'instagram.com' in url.lower():
        match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
        if match:
            shortcode = match.group(1)
            embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
            try:
                r = requests.get(embed_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    html = r.text
                    # Buscamos si hay un video oculto en el embed
                    video_match = re.search(r'"video_url":"([^"]+)"', html)
                    if video_match:
                        v_url = video_match.group(1).encode('utf-8').decode('unicode_escape').replace('\\/', '/')
                        return {
                            'tipo': 'video',
                            'titulo': f'Instagram_{shortcode}',
                            'autor': 'Instagram',
                            'miniatura': '',
                            'formatos': [{'id': 'directo', 'etiqueta': 'Video HD (Original)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': v_url}]
                        }
                    
                    # Buscamos la imagen principal de alta resolución en el embed
                    soup = BeautifulSoup(html, 'html.parser')
                    img_tag = soup.find('img', class_='EmbeddedMediaImage')
                    if img_tag and img_tag.get('src'):
                        img_url = img_tag['src'].replace('&amp;', '&')
                        return {
                            'tipo': 'imagen',
                            'titulo': f'IG_Foto_{shortcode}',
                            'autor': 'Instagram',
                            'miniatura': img_url,
                            'formatos': [{'id': 'img_hd', 'etiqueta': 'Imagen en Máxima Resolución (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_url}]
                        }
            except Exception as e:
                print(f"Error en embed IG: {e}")

    # 2. Intento vía Metadatos OpenGraph (Funciona para fotos de IG, Pinterest, X, FB)
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        og_image = soup.find('meta', property='og:image')
        og_title = soup.find('meta', property='og:title')
        
        titulo = og_title['content'] if (og_title and og_title.get('content')) else 'SnapDrop_Media'
        
        if og_video and og_video.get('content'):
            v_url = og_video['content']
            thumb = og_image['content'] if (og_image and og_image.get('content')) else ''
            return {
                'tipo': 'video',
                'titulo': titulo[:60],
                'autor': 'Red Social',
                'miniatura': thumb,
                'formatos': [{'id': 'directo', 'etiqueta': 'Video Original (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': v_url}]
            }
            
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            return {
                'tipo': 'imagen',
                'titulo': titulo[:60],
                'autor': 'Red Social',
                'miniatura': img_url,
                'formatos': [{'id': 'img_hd', 'etiqueta': 'Imagen Original (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_url}]
            }
    except Exception as e:
        print(f"Error en OpenGraph fallback: {e}")

    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analizar', methods=['POST'])
@limiter.limit("15 per minute")
def analizar_enlace():
    url_cruda = request.form.get('enlace', '')
    url = limpiar_y_expandir_url(url_cruda)
    if not url:
        return jsonify({'status': 'error', 'message': 'Ingresa un enlace válido.'})

    ahora = time.time()
    if url in cache_analisis:
        ts, datos = cache_analisis[url]
        if ahora - ts < TIEMPO_CACHE:
            return jsonify(datos)

    opciones = {
        'quiet': True,
        'skip_download': True,
        'nocheckcertificate': True,
        'socket_timeout': 15,
        'retries': 2,
        'noplaylist': True,
        'playlist_items': '1',
        'user_agent': USER_AGENT_PRO,
        'referer': 'https://www.tiktok.com/' if 'tiktok' in url.lower() else url,
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            duracion = info.get('duration') or 0
            if duracion > LIMITE_DURACION:
                return jsonify({'status': 'error', 'message': 'El video supera el límite de 20 minutos.'})

            titulo = (info.get('title') or 'SnapDrop_Media')[:65]
            autor = info.get('uploader') or info.get('creator') or 'Red Social'
            miniatura = info.get('thumbnail') or ''
            formatos_yt = info.get('formats') or []

            # Analizamos las resoluciones de video reales que existen en el post
            alturas_disponibles = set()
            tiene_video = False
            for f in formatos_yt:
                vcodec = f.get('vcodec')
                height = f.get('height')
                if vcodec and vcodec != 'none':
                    tiene_video = True
                    if height:
                        alturas_disponibles.add(height)

            if not tiene_video and (info.get('url') or miniatura):
                # Es una imagen pura detectada por yt-dlp
                img_final = info.get('url') or miniatura
                respuesta = {
                    'status': 'success',
                    'tipo': 'imagen',
                    'titulo': titulo,
                    'autor': autor,
                    'miniatura': img_final,
                    'url_origen': url,
                    'formatos': [
                        {'id': 'img_original', 'etiqueta': 'Imagen en Máxima Calidad (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_final}
                    ]
                }
                cache_analisis[url] = (ahora, respuesta)
                return jsonify(respuesta)

            # Si es video, armamos la lista dinámica de resoluciones reales
            alturas_ordenadas = sorted(list(alturas_disponibles), reverse=True)
            lista_formatos = []

            if alturas_ordenadas:
                for h in alturas_ordenadas:
                    if h >= 360: # Filtramos miniaturas rotas o resoluciones demasiado bajas
                        etiqueta = f"Video {h}p {'(Full HD)' if h >= 1080 else '(HD)' if h >= 720 else ''}".strip()
                        lista_formatos.append({
                            'id': str(h),
                            'etiqueta': etiqueta,
                            'ext': 'mp4',
                            'modo': 'servidor',
                            'url_directa': ''
                        })

            # Si no reportó alturas específicas o como opción principal siempre aseguramos la máxima calidad
            if not lista_formatos:
                lista_formatos.append({
                    'id': 'best',
                    'etiqueta': 'Video en Máxima Calidad (Original)',
                    'ext': 'mp4',
                    'modo': 'servidor',
                    'url_directa': ''
                })

            # Añadimos opción de solo Audio MP3
            lista_formatos.append({
                'id': 'audio',
                'etiqueta': 'Solo Audio (MP3 + Portada)',
                'ext': 'mp3',
                'modo': 'servidor',
                'url_directa': ''
            })

            # Y opción de bajar solo la portada/miniatura si el usuario la quiere como foto
            if miniatura:
                lista_formatos.append({
                    'id': 'miniatura',
                    'etiqueta': 'Solo Foto de Portada (JPG)',
                    'ext': 'jpg',
                    'modo': 'directo',
                    'url_directa': miniatura
                })

            respuesta = {
                'status': 'success',
                'tipo': 'video',
                'titulo': titulo,
                'autor': autor,
                'miniatura': miniatura,
                'url_origen': url,
                'formatos': lista_formatos
            }
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

    except Exception as e:
        print(f"yt-dlp no pudo extraer ({e}), activando motor de respaldo para imágenes...")
        respaldo = extraer_respaldo_imagen_o_ig(url)
        if respaldo:
            respuesta = {
                'status': 'success',
                'tipo': respaldo['tipo'],
                'titulo': respaldo['titulo'],
                'autor': respaldo['autor'],
                'miniatura': respaldo['miniatura'],
                'url_origen': url,
                'formatos': respaldo['formatos']
            }
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

        return jsonify({
            'status': 'error',
            'message': 'No se pudo extraer el medio. Verifica que la publicación sea pública.'
        })

@app.route('/descargar')
def descargar_archivo():
    url_origen = limpiar_y_expandir_url(urllib.parse.unquote(request.args.get('url', '')))
    url_directa = urllib.parse.unquote(request.args.get('directa', ''))
    titulo_crudo = urllib.parse.unquote(request.args.get('titulo', 'SnapDrop'))
    ext = request.args.get('ext', 'mp4')
    modo = request.args.get('modo', 'servidor')
    res_id = request.args.get('res', 'best')

    titulo = re.sub(r'[^\w\s-]', '', titulo_crudo).strip().replace(' ', '_')[:50] or 'SnapDrop'

    if modo == 'directo' and url_directa:
        r = requests.get(url_directa, stream=True, headers={'User-Agent': USER_AGENT_PRO}, timeout=20)
        return Response(stream_with_context(r.iter_content(chunk_size=8192)), headers={
            "Content-Disposition": f'attachment; filename="{titulo}.{ext}"',
            "Content-Type": "application/octet-stream"
        })

    id_unico = uuid.uuid4().hex[:8]
    temp_path_base = os.path.join(tempfile.gettempdir(), f"{titulo}_{id_unico}")
    expected_file = f"{temp_path_base}.{ext}"

    if res_id == 'audio':
        formato_bajada = 'bestaudio/best'
    elif res_id.isdigit():
        formato_bajada = f'bestvideo[height<={res_id}]+bestaudio/best[height<={res_id}]/best[height<={res_id}]/best'
    else:
        formato_bajada = 'bestvideo+bestaudio/best'

    opciones_bajada = {
        'outtmpl': f"{temp_path_base}.%(ext)s",
        'format': formato_bajada,
        'quiet': True,
        'nocheckcertificate': True,
        'socket_timeout': 25,
        'retries': 3,
        'user_agent': USER_AGENT_PRO,
        'referer': 'https://www.tiktok.com/' if 'tiktok' in url_origen.lower() else url_origen,
    }

    if ext == 'mp3':
        opciones_bajada['writethumbnail'] = True
        opciones_bajada['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'EmbedThumbnail'},
            {'key': 'FFmpegMetadata'}
        ]
    else:
        opciones_bajada['merge_output_format'] = 'mp4'
        opciones_bajada['postprocessors'] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]

    with yt_dlp.YoutubeDL(opciones_bajada) as ydl:
        ydl.download([url_origen])

    def stream_and_remove():
        try:
            with open(expected_file, 'rb') as f:
                yield from f
        finally:
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
