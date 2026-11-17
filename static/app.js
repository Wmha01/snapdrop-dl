function mostrarToast(mensaje, esError = false) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${esError ? 'error' : ''}`;
    toast.innerHTML = `<span style="font-size: 18px;">${esError ? '⚠️' : '✨'}</span> ${mensaje}`;
    container.appendChild(toast);
    
    setTimeout(() => {
        if(container.contains(toast)) { toast.remove(); }
    }, 3900);
}

let promptDiferido;
const btnInstalar = document.getElementById('btn-install');

window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    promptDiferido = e;
    btnInstalar.style.display = 'flex';
});

btnInstalar.addEventListener('click', async () => {
    if (promptDiferido) {
        promptDiferido.prompt();
        const { outcome } = await promptDiferido.userChoice;
        if (outcome === 'accepted') {
            btnInstalar.style.display = 'none';
            mostrarToast('¡Gracias por instalar SnapDrop!');
        }
        promptDiferido = null;
    }
});

const form = document.getElementById('form-ajax');
const input = document.getElementById('input-enlace');
const btnClearInput = document.getElementById('btn-clear-input');
const btnGenerar = document.getElementById('btn-generar');
const contenedorResultado = document.getElementById('resultado-dinamico');
const pCont = document.getElementById('p-cont');
const pBar = document.getElementById('p-bar');
const status = document.getElementById('status-text');

function actualizarEstadoInput() {
    const valor = input.value.trim();
    btnGenerar.disabled = !valor.startsWith('http');
    btnClearInput.style.display = valor.length > 0 ? 'block' : 'none';
}

input.addEventListener('input', actualizarEstadoInput);

btnClearInput.addEventListener('click', () => {
    input.value = '';
    actualizarEstadoInput();
    input.focus();
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btnGenerar.style.display = 'none';
    contenedorResultado.style.display = 'none';
    
    pCont.style.display = 'block';
    status.style.display = 'block';
    pBar.style.width = '10%';
    status.innerText = 'Conectando y desbloqueando enlace...';

    const eventSource = new EventSource('/progreso');
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        pBar.style.width = data.porcentaje + '%';
        status.innerText = `Procesando medio... ${Math.round(data.porcentaje)}%`;
        if(data.porcentaje >= 100) eventSource.close();
    };
    eventSource.onerror = () => eventSource.close();

    const formData = new FormData(form);
    try {
        const response = await fetch('/procesar', { method: 'POST', body: formData });
        const data = await response.json();
        
        eventSource.close();
        pCont.style.display = 'none';
        status.style.display = 'none';
        btnGenerar.style.display = 'block';
        contenedorResultado.style.display = 'block';

        if (data.status === 'success') {
            const urlCompletaDescarga = window.location.origin + data.url_descarga;
            const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(urlCompletaDescarga)}`;

            contenedorResultado.innerHTML = `
                <div class="resultado-card">
                    <p style="margin:0; font-weight: 600; color: var(--text-main); font-size: 14px;">${data.titulo}</p>
                    <p class="meta-info">👤 ${data.autor} • 📦 Formato ${data.ext.toUpperCase()}</p>
                    <img src="${data.miniatura}" class="miniatura" onerror="this.style.display='none'">
                    <a href="${data.url_descarga}" class="btn-descarga">⬇️ Descargar Ahora</a>
                    
                    <div class="acciones-extra">
                        <button type="button" class="btn-secundario" onclick="toggleQR()">📱 Pasar al Celular (QR)</button>
                        <button type="button" class="btn-secundario" onclick="compartirEnlace('${urlCompletaDescarga}', '${data.titulo.replace(/'/g, "")}')">🔗 Compartir</button>
                    </div>

                    <div id="qr-box" class="qr-box">
                        <img src="${qrApiUrl}" alt="Código QR de descarga">
                        <p>Escanea con tu cámara para descargar en el móvil</p>
                    </div>
                </div>`;
            guardarHistorial(data.titulo);
            mostrarToast('¡Listo para descargar!'); 
        } else {
            contenedorResultado.innerHTML = `<div class="resultado-card" style="border-color: var(--danger); color: var(--danger);">⚠️ ${data.message}</div>`;
            mostrarToast(data.message, true); 
        }
    } catch (error) { 
        eventSource.close();
        pCont.style.display = 'none';
        status.style.display = 'none';
        btnGenerar.style.display = 'block';
        mostrarToast('Error de conexión con el servidor.', true); 
    }
});

function toggleQR() {
    const box = document.getElementById('qr-box');
    if (box) {
        box.style.display = box.style.display === 'block' ? 'none' : 'block';
    }
}

async function compartirEnlace(url, titulo) {
    if (navigator.share) {
        try {
            await navigator.share({ title: titulo, text: 'Descargado con SnapDrop:', url: url });
        } catch (err) {}
    } else {
        await navigator.clipboard.writeText(url);
        mostrarToast('Enlace de descarga copiado al portapapeles');
    }
}

function guardarHistorial(titulo) {
    let hist = JSON.parse(localStorage.getItem('pdp_historial') || '[]');
    hist.unshift({ titulo, fecha: new Date().toLocaleDateString() });
    localStorage.setItem('pdp_historial', JSON.stringify(hist.slice(0, 5)));
    renderHistorial();
}

function renderHistorial() {
    const lista = document.getElementById('lista-historial');
    const data = JSON.parse(localStorage.getItem('pdp_historial') || '[]');
    lista.innerHTML = data.length ? '' : '<li style="text-align:center; color:var(--text-secondary); font-size:13px; padding: 15px 0;">No hay descargas recientes</li>';
    data.forEach(i => { lista.innerHTML += `<li class="historial-item"><span>${i.titulo}</span><small>${i.fecha}</small></li>`; });
}

document.getElementById('btn-pegar').onclick = async () => { 
    try { 
        input.value = await navigator.clipboard.readText(); 
        actualizarEstadoInput();
        if(input.value) mostrarToast('Enlace pegado del portapapeles');
    } catch(e) {
        mostrarToast('Permiso de portapapeles denegado', true);
    } 
};

document.getElementById('btn-limpiar').onclick = () => { 
    localStorage.removeItem('pdp_historial'); 
    renderHistorial(); 
    mostrarToast('Historial limpiado');
};

function toggleDarkMode() { 
    document.body.classList.toggle('dark-mode'); 
    localStorage.setItem('pdp_theme', document.body.classList.contains('dark-mode') ? 'dark' : 'light');
}

if(localStorage.getItem('pdp_theme') === 'dark') document.body.classList.add('dark-mode');

renderHistorial();
