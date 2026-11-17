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
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://",
)

cache_analisis = {}
TIEMPO_CACHE = 600
LIMITE_DURACION = 1800  # 30 minutos

USER_AGENT_PC = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
# User-Agent de Bot Oficial para saltar el muro de login en metadatos de Instagram/FB
USER_AGENT_BOT = 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)'

def limpiar_y_expandir_url(url_cruda):
    if not url_cruda:
        return ""
    url = url_cruda.strip()
    if any(short in url.lower() for short in ['vt.tiktok.com', 'vm.tiktok.com', 'tiktok.com/t/', 'pin.it', 'youtu.be']):
        try:
            resp = requests.head(url, allow_redirects=True, timeout=8, headers={'User-Agent': USER_AGENT_PC})
            url = resp.url
        except Exception:
            pass
    return url

def proxificar_imagen(url_img):
    """Pasa las miniaturas de Instagram por nuestro propio servidor para que el navegador no las bloquee por CORS."""
    if not url_img:
        return ""
    if 'cdninstagram.com' in url_img or 'fbcdn.net' in url_img:
        return f"/proxy-img?url={urllib.parse.quote(url_img, safe='')}"
    return url_img

# =========================================================================
# MOTORES ESPECIALIZADOS ANTIBLOQUEO DE DATACENTER (TIKTOK, IG, X, YOUTUBE)
# =========================================================================

def motor_tiktok_api(url):
    """Extrae videos, audios e imágenes de TikTok sin bloqueo 503 en Render."""
    try:
        api_url = "https://www.tikwm.com/api/"
        r = requests.post(api_url, data={'url': url, 'hd': 1}, timeout=12)
        data = r.json()
        if data.get('code') == 0 and data.get('data'):
            d = data['data']
            titulo = (d.get('title') or 'TikTok_Media')[:65]
            autor = d.get('author', {}).get('nickname') or 'TikTok'
            miniatura = d.get('cover') or ''
            
            # Si es un carrusel de fotos de TikTok
            if d.get('images'):
                formatos = []
                for idx, img_url in enumerate(d['images']):
                    formatos.append({
                        'id': f'img_{idx+1}',
                        'etiqueta': f'Imagen #{idx+1} en Alta Calidad (JPG)',
                        'ext': 'jpg',
                        'modo': 'directo',
                        'url_directa': img_url
                    })
                if d.get('music'):
                    formatos.append({
                        'id': 'audio',
                        'etiqueta': 'Solo Audio del Post (MP3)',
                        'ext': 'mp3',
                        'modo': 'directo',
                        'url_directa': d['music']
                    })
                return {
                    'tipo': 'imagen',
                    'titulo': titulo,
                    'autor': autor,
                    'miniatura': d['images'][0],
                    'formatos': formatos
                }

            # Si es un video de TikTok
            formatos = []
            if d.get('hdplay'):
                formatos.append({'id': '1080p', 'etiqueta': 'Video Full HD (Sin Marca de Agua)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': d['hdplay']})
            if d.get('play'):
                formatos.append({'id': '720p', 'etiqueta': 'Video HD Estándar (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': d['play']})
            if d.get('wmplay'):
                formatos.append({'id': 'wm', 'etiqueta': 'Video con Marca de Agua (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': d['wmplay']})
            if d.get('music'):
                formatos.append({'id': 'audio', 'etiqueta': 'Solo Audio Original (MP3)', 'ext': 'mp3', 'modo': 'directo', 'url_directa': d['music']})

            if formatos:
                return {
                    'tipo': 'video',
                    'titulo': titulo,
                    'autor': autor,
                    'miniatura': miniatura,
                    'formatos': formatos
                }
    except Exception as e:
        print(f"Error en motor TikWM: {e}")
    return None

def motor_x_twitter_api(url):
    """Usa la API abierta de VxTwitter para extraer videos y fotos de X sin bloqueo."""
    try:
        match = re.search(r'(?:twitter\.com|x\.com)/([^/]+)/status/(\d+)', url)
        if not match:
            return None
        usuario, status_id = match.group(1), match.group(2)
        api_url = f"https://api.vxtwitter.com/{usuario}/status/{status_id}"
        r = requests.get(api_url, timeout=10)
        if r.status_code == 200:
            d = r.json()
            titulo = (d.get('text') or f'Post de @{usuario}')[:65]
            autor = d.get('user_name') or f'@{usuario}'
            media = d.get('media_extended') or []
            if not media:
                return None

            formatos = []
            es_video = False
            miniatura = media[0].get('thumbnail_url') or media[0].get('url') or ''

            for idx, m in enumerate(media):
                m_type = m.get('type')
                m_url = m.get('url')
                if m_type in ['video', 'gif'] and m_url:
                    es_video = True
                    formatos.append({
                        'id': f'vid_{idx+1}',
                        'etiqueta': f'Video HD #{idx+1} (MP4)',
                        'ext': 'mp4',
                        'modo': 'directo',
                        'url_directa': m_url
                    })
                elif m_type == 'image' and m_url:
                    formatos.append({
                        'id': f'img_{idx+1}',
                        'etiqueta': f'Imagen HD #{idx+1} (JPG)',
                        'ext': 'jpg',
                        'modo': 'directo',
                        'url_directa': m_url
                    })

            if formatos:
                return {
                    'tipo': 'video' if es_video else 'imagen',
                    'titulo': titulo,
                    'autor': autor,
                    'miniatura': miniatura,
                    'formatos': formatos
                }
    except Exception as e:
        print(f"Error en motor VxTwitter: {e}")
    return None

def motor_instagram_profundo(url):
    """Extrae fotos, carruseles y reels de Instagram saltando el bloqueo de Datacenter."""
    match = re.search(r'/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)', url)
    if not match:
        return None
    shortcode = match.group(1)

    # 1. Intento vía Embed Captioned con firma de navegador limpio
    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    try:
        r = requests.get(embed_url, headers={'User-Agent': USER_AGENT_PC, 'Accept-Language': 'es-ES,es;q=0.9'}, timeout=12)
        if r.status_code == 200:
            html = r.text
            soup = BeautifulSoup(html, 'html.parser')
            
            # Buscamos autor
            user_tag = soup.find('span', class_='UsernameText')
            autor = user_tag.text.strip() if user_tag else 'Instagram'

            # Verificamos si contiene video_url en el JSON interno del embed
            video_matches = re.findall(r'"video_url"\s*:\s*"([^"]+)"', html)
            if video_matches:
                v_url = video_matches[0].encode('utf-8').decode('unicode_escape').replace('\\/', '/')
                img_tag = soup.find('img', class_='EmbeddedMediaImage')
                thumb = img_tag['src'].replace('&amp;', '&') if (img_tag and img_tag.get('src')) else ''
                return {
                    'tipo': 'video',
                    'titulo': f'Instagram Reel/Video ({shortcode})',
                    'autor': autor,
                    'miniatura': proxificar_imagen(thumb),
                    'formatos': [{'id': 'hd', 'etiqueta': 'Video en Máxima Calidad (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': v_url}]
                }

            # Buscamos todas las imágenes de alta resolución dentro del Embed (soporta fotos individuales y carruseles)
            formatos_img = []
            urls_vistas = set()

            # Búsqueda 1: Etiquetas display_url en el script del embed
            display_matches = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html)
            for d_url in display_matches:
                clean_img = d_url.encode('utf-8').decode('unicode_escape').replace('\\/', '/')
                if clean_img not in urls_vistas:
                    urls_vistas.add(clean_img)
                    formatos_img.append({
                        'id': f'img_{len(formatos_img)+1}',
                        'etiqueta': f'Imagen #{len(formatos_img)+1} en Resolución Original (JPG)' if len(display_matches) > 1 else 'Imagen en Máxima Resolución (JPG)',
                        'ext': 'jpg',
                        'modo': 'directo',
                        'url_directa': clean_img
                    })

            # Búsqueda 2: Etiqueta EmbeddedMediaImage directa
            if not formatos_img:
                img_tag = soup.find('img', class_='EmbeddedMediaImage')
                if img_tag and img_tag.get('src'):
                    img_url = img_tag['src'].replace('&amp;', '&')
                    formatos_img.append({
                        'id': 'img_1',
                        'etiqueta': 'Imagen en Máxima Resolución (JPG)',
                        'ext': 'jpg',
                        'modo': 'directo',
                        'url_directa': img_url
                    })

            if formatos_img:
                primera_img = formatos_img[0]['url_directa']
                return {
                    'tipo': 'imagen',
                    'titulo': f'Publicación de {autor} ({shortcode})',
                    'autor': autor,
                    'miniatura': proxificar_imagen(primera_img),
                    'formatos': formatos_img
                }
    except Exception as e:
        print(f"Error en IG Embed: {e}")

    # 2. Intento vía Crawler Oficial (facebookexternalhit salta el login wall de IG)
    try:
        r_bot = requests.get(f"https://www.instagram.com/p/{shortcode}/", headers={'User-Agent': USER_AGENT_BOT}, timeout=10)
        if r_bot.status_code == 200:
            soup = BeautifulSoup(r_bot.text, 'html.parser')
            og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
            og_image = soup.find('meta', property='og:image')
            og_title = soup.find('meta', property='og:title')
            titulo = og_title['content'][:65] if (og_title and og_title.get('content')) else f'Instagram_{shortcode}'

            if og_video and og_video.get('content'):
                thumb = og_image['content'] if (og_image and og_image.get('content')) else ''
                return {
                    'tipo': 'video',
                    'titulo': titulo,
                    'autor': 'Instagram',
                    'miniatura': proxificar_imagen(thumb),
                    'formatos': [{'id': 'hd', 'etiqueta': 'Video Original (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': og_video['content']}]
                }
            if og_image and og_image.get('content'):
                img_url = og_image['content'].replace('&amp;', '&')
                return {
                    'tipo': 'imagen',
                    'titulo': titulo,
                    'autor': 'Instagram',
                    'miniatura': proxificar_imagen(img_url),
                    'formatos': [{'id': 'img_hd', 'etiqueta': 'Imagen en Alta Calidad (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_url}]
                }
    except Exception as e:
        print(f"Error en IG Bot Fallback: {e}")

    return None

def motor_opengraph_universal(url):
    """Respaldo para Pinterest, Facebook y otras webs usando firma de Crawler."""
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT_BOT}, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        og_video = soup.find('meta', property='og:video') or soup.find('meta', property='og:video:secure_url')
        og_image = soup.find('meta', property='og:image')
        og_title = soup.find('meta', property='og:title')
        titulo = og_title['content'][:65] if (og_title and og_title.get('content')) else 'SnapDrop_Media'

        if og_video and og_video.get('content'):
            thumb = og_image['content'] if (og_image and og_image.get('content')) else ''
            return {
                'tipo': 'video',
                'titulo': titulo,
                'autor': 'Red Social',
                'miniatura': proxificar_imagen(thumb),
                'formatos': [{'id': 'hd', 'etiqueta': 'Video Original (MP4)', 'ext': 'mp4', 'modo': 'directo', 'url_directa': og_video['content']}]
            }
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            return {
                'tipo': 'imagen',
                'titulo': titulo,
                'autor': 'Red Social',
                'miniatura': proxificar_imagen(img_url),
                'formatos': [{'id': 'img_hd', 'etiqueta': 'Imagen Original (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_url}]
            }
    except Exception:
        pass
    return None

# =========================================================================
# RUTAS DE LA APLICACIÓN
# =========================================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/proxy-img')
def proxy_img():
    """Sirve imágenes protegidas por CORS (como las de Instagram) para que se vean en la web."""
    url_img = urllib.parse.unquote(request.args.get('url', ''))
    if not url_img.startswith('http'):
        return '', 404
    try:
        r = requests.get(url_img, headers={'User-Agent': USER_AGENT_PC, 'Referer': 'https://www.instagram.com/'}, stream=True, timeout=10)
        return Response(stream_with_context(r.iter_content(chunk_size=4096)), content_type=r.headers.get('Content-Type', 'image/jpeg'))
    except Exception:
        return '', 404

@app.route('/analizar', methods=['POST'])
@limiter.limit("20 per minute")
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

    url_lower = url.lower()

    # 1. Si es TikTok, usamos primero el motor especializado anti-503
    if 'tiktok.com' in url_lower:
        res_tt = motor_tiktok_api(url)
        if res_tt:
            respuesta = {'status': 'success', 'url_origen': url, **res_tt}
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

    # 2. Si es X / Twitter, usamos la API abierta de VxTwitter (soporta fotos y videos)
    if 'twitter.com' in url_lower or 'x.com' in url_lower:
        res_x = motor_x_twitter_api(url)
        if res_x:
            respuesta = {'status': 'success', 'url_origen': url, **res_x}
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

    # 3. Si es Instagram, probamos primero el extractor profundo (soporta fotos, carruseles y reels sin login)
    if 'instagram.com' in url_lower:
        res_ig = motor_instagram_profundo(url)
        if res_ig:
            respuesta = {'status': 'success', 'url_origen': url, **res_ig}
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

    # 4. Motor General yt-dlp con clientes móviles activados (para YouTube, FB, Pinterest, etc.)
    opciones = {
        'quiet': True,
        'skip_download': True,
        'nocheckcertificate': True,
        'socket_timeout': 15,
        'retries': 2,
        'noplaylist': True,
        'playlist_items': '1',
        'user_agent': USER_AGENT_PC,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'web_creator'],
                'player_skip': ['webpage', 'configs']
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            duracion = info.get('duration') or 0
            if duracion > LIMITE_DURACION:
                return jsonify({'status': 'error', 'message': 'El video supera el límite de 30 minutos.'})

            titulo = (info.get('title') or 'SnapDrop_Media')[:65]
            autor = info.get('uploader') or info.get('creator') or 'Red Social'
            miniatura = proxificar_imagen(info.get('thumbnail') or '')
            formatos_yt = info.get('formats') or []

            alturas_disponibles = set()
            url_directa_por_altura = {}
            tiene_video = False

            for f in formatos_yt:
                vcodec = f.get('vcodec')
                acodec = f.get('acodec')
                height = f.get('height')
                f_url = f.get('url')
                if vcodec and vcodec != 'none':
                    tiene_video = True
                    if height and height >= 360:
                        alturas_disponibles.add(height)
                        # Si el formato ya tiene video+audio juntos en HTTP directo, lo guardamos
                        if acodec and acodec != 'none' and f_url and '.m3u8' not in f_url:
                            url_directa_por_altura[height] = f_url

            if not tiene_video and (info.get('url') or miniatura):
                img_final = info.get('url') or info.get('thumbnail')
                respuesta = {
                    'status': 'success',
                    'tipo': 'imagen',
                    'titulo': titulo,
                    'autor': autor,
                    'miniatura': miniatura,
                    'url_origen': url,
                    'formatos': [
                        {'id': 'img_original', 'etiqueta': 'Imagen en Máxima Calidad (JPG)', 'ext': 'jpg', 'modo': 'directo', 'url_directa': img_final}
                    ]
                }
                cache_analisis[url] = (ahora, respuesta)
                return jsonify(respuesta)

            alturas_ordenadas = sorted(list(alturas_disponibles), reverse=True)
            lista_formatos = []

            for h in alturas_ordenadas:
                etiqueta = f"Video {h}p {'(Full HD)' if h >= 1080 else '(HD)' if h >= 720 else ''}".strip()
                f_directa = url_directa_por_altura.get(h, '')
                lista_formatos.append({
                    'id': str(h),
                    'etiqueta': etiqueta,
                    'ext': 'mp4',
                    'modo': 'directo' if f_directa else 'servidor',
                    'url_directa': f_directa
                })

            if not lista_formatos:
                d_url = info.get('url') or ''
                es_directo = bool(d_url and '.m3u8' not in d_url)
                lista_formatos.append({
                    'id': 'best',
                    'etiqueta': 'Video en Máxima Calidad (MP4)',
                    'ext': 'mp4',
                    'modo': 'directo' if es_directo else 'servidor',
                    'url_directa': d_url if es_directo else ''
                })

            lista_formatos.append({
                'id': 'audio',
                'etiqueta': 'Solo Audio (MP3 + Portada)',
                'ext': 'mp3',
                'modo': 'servidor',
                'url_directa': ''
            })

            if info.get('thumbnail'):
                lista_formatos.append({
                    'id': 'miniatura',
                    'etiqueta': 'Solo Foto de Portada (JPG)',
                    'ext': 'jpg',
                    'modo': 'directo',
                    'url_directa': info.get('thumbnail')
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
        print(f"Fallo en yt-dlp ({e}), probando motor OpenGraph universal...")
        res_og = motor_opengraph_universal(url)
        if res_og:
            respuesta = {'status': 'success', 'url_origen': url, **res_og}
            cache_analisis[url] = (ahora, respuesta)
            return jsonify(respuesta)

        return jsonify({
            'status': 'error',
            'message': 'No se pudo extraer el medio. Verifica que el enlace exista y sea público.'
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

    # Descarga en streaming directo (Fotos de IG, TikToks por API, Videos de X)
    if modo == 'directo' and url_directa:
        headers = {'User-Agent': USER_AGENT_PC}
        if 'cdninstagram.com' in url_directa or 'fbcdn.net' in url_directa:
            headers['Referer'] = 'https://www.instagram.com/'
        r = requests.get(url_directa, stream=True, headers=headers, timeout=25)
        return Response(stream_with_context(r.iter_content(chunk_size=16384)), headers={
            "Content-Disposition": f'attachment; filename="{titulo}.{ext}"',
            "Content-Type": "application/octet-stream"
        })

    # Descarga y conversión asistida por FFmpeg en el servidor
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
        'user_agent': USER_AGENT_PC,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android', 'web_creator'],
                'player_skip': ['webpage', 'configs']
            }
        }
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

    try:
        with yt_dlp.YoutubeDL(opciones_bajada) as ydl:
            ydl.download([url_origen])
    except Exception as e:
        return f"Error al procesar la descarga en el servidor: {e}", 500

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
