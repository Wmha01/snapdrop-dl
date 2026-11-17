function mostrarToast(mensaje, esError = false) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${esError ? 'error' : ''}`;
    toast.innerHTML = `<span>${esError ? '⚠️' : '✨'}</span> ${mensaje}`;
    container.appendChild(toast);
    setTimeout(() => { if (container.contains(toast)) toast.remove(); }, 3600);
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
        if (outcome === 'accepted') btnInstalar.style.display = 'none';
        promptDiferido = null;
    }
});

const form = document.getElementById('form-ajax');
const input = document.getElementById('input-enlace');
const btnClearInput = document.getElementById('btn-clear-input');
const btnGenerar = document.getElementById('btn-generar');
const contenedorResultado = document.getElementById('resultado-dinamico');
const loaderBox = document.getElementById('loader-box');

let datosActuales = null;

function actualizarEstadoInput() {
    const valor = input.value.trim();
    btnGenerar.disabled = !valor.startsWith('http');
    btnClearInput.style.display = valor.length > 0 ? 'block' : 'none';
}

input.addEventListener('input', actualizarEstadoInput);

btnClearInput.addEventListener('click', () => {
    input.value = '';
    actualizarEstadoInput();
    contenedorResultado.style.display = 'none';
    input.focus();
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btnGenerar.disabled = true;
    contenedorResultado.style.display = 'none';
    loaderBox.style.display = 'block';

    const formData = new FormData(form);
    try {
        const response = await fetch('/analizar', { method: 'POST', body: formData });
        const data = await response.json();
        
        loaderBox.style.display = 'none';
        btnGenerar.disabled = false;
        contenedorResultado.style.display = 'block';

        if (data.status === 'success') {
            datosActuales = data;
            const etiquetaTipo = data.tipo === 'imagen' ? '📸 Imagen Detectada' : '🎬 Video Detectado';
            
            let opcionesHTML = '';
            data.formatos.forEach((f, idx) => {
                opcionesHTML += `<option value="${idx}">${f.etiqueta}</option>`;
            });

            contenedorResultado.innerHTML = `
                <div class="resultado-card">
                    <span class="badge-tipo">${etiquetaTipo}</span>
                    <p class="titulo-media">${data.titulo}</p>
                    <p class="meta-info">Por ${data.autor}</p>
                    ${data.miniatura ? `<img src="${data.miniatura}" class="miniatura" onerror="this.style.display='none'">` : ''}
                    
                    <label class="selector-label">Elige formato y resolución:</label>
                    <select id="select-formato-dinamico" class="select-resolucion">
                        ${opcionesHTML}
                    </select>

                    <button type="button" id="btn-ejecutar-descarga" class="btn-descarga">Descargar Archivo</button>
                </div>`;

            document.getElementById('btn-ejecutar-descarga').onclick = iniciarDescargaSeleccionada;
            guardarHistorial(data.titulo);
        } else {
            contenedorResultado.innerHTML = `
                <div class="resultado-card" style="border-color: var(--danger); color: var(--danger); text-align: center;">
                    ⚠️ ${data.message}
                </div>`;
            mostrarToast(data.message, true);
        }
    } catch (error) {
        loaderBox.style.display = 'none';
        btnGenerar.disabled = false;
        mostrarToast('Error de conexión con el servidor.', true);
    }
});

function iniciarDescargaSeleccionada() {
    if (!datosActuales) return;
    const idx = document.getElementById('select-formato-dinamico').value;
    const formatoElegido = datosActuales.formatos[idx];

    const params = new URLSearchParams({
        url: datosActuales.url_origen,
        directa: formatoElegido.url_directa || '',
        titulo: datosActuales.titulo,
        ext: formatoElegido.ext,
        modo: formatoElegido.modo,
        res: formatoElegido.id
    });

    mostrarToast('Iniciando descarga...');
    window.location.href = `/descargar?${params.toString()}`;
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
    lista.innerHTML = data.length ? '' : '<li style="text-align:center; color:var(--text-secondary); font-size:12px; padding: 12px 0;">No hay descargas recientes</li>';
    data.forEach(i => { lista.innerHTML += `<li class="historial-item"><span>${i.titulo}</span><small>${i.fecha}</small></li>`; });
}

document.getElementById('btn-pegar').onclick = async () => { 
    try { 
        input.value = await navigator.clipboard.readText(); 
        actualizarEstadoInput();
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
