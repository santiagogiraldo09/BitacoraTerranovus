// =================================================================
//          VARIABLES GLOBALES
// =================================================================
let currentStream = null;
const capturedPhotos = [];
const capturedVideos = [];

// Variables para la grabación de video de la cámara
let videoMediaRecorder;
let videoChunks = [];

// Variables para la grabación de audio por campo
let audioMediaRecorder;
let audioFieldChunks = [];
let isFieldRecording = false;
let currentTargetInput = null;

let contadorFinalizadas = 0;
let contadorPendientes = 0;
let contadorFacturar = 0;
let contadorSeguridad = 0;
let contadorAmbiental = 0;
let contadorCalidad = 0;

let currenRecognition = null;
var itemMediaData = {};
var recordedChunks = [];


// =================================================================
//          INICIALIZACIÓN DE EVENTOS
// =================================================================
document.addEventListener('DOMContentLoaded', () => {
    // Listener para el botón de activar cámara
    /*
    document.getElementById('activate-camera-btn').addEventListener('click', () => {
        startCamera();
        document.getElementById('activate-camera-btn').style.display = 'none';
    });
    */

    // Listeners para los controles de la cámara
    document.getElementById('start-record-btn').addEventListener('click', startVideoRecording);
    document.getElementById('stop-record-btn').addEventListener('click', stopVideoRecording);

    // Listeners para adjuntar archivos
    /*
    document.getElementById('file-input').addEventListener('change', handleFileUpload);
    document.getElementById('video-file-input').addEventListener('change', handleVideoUpload);
    */

    // Listeners para grabación de audio por campo
    document.querySelectorAll('.record-btn').forEach(button => {
        button.addEventListener('click', () => startFieldRecording(button));
    });
    document.querySelectorAll('.stop-btn').forEach(button => {
        button.addEventListener('click', stopFieldRecording);
    });
});

// =================================================================
//          FUNCIONES DE CÁMARA (FOTO Y VIDEO)
// =================================================================
async function startCamera() {
    const videoElement = document.getElementById('videoElement');
    const cameraContainer = document.getElementById('camera-container');
    const actionButtons = document.querySelector('.action-buttons-wrapper');
    const stopRecordButton = document.getElementById('stop-record-btn');

    // Configuración inicial de botones
    document.getElementById('start-record-btn').style.display = 'flex';
    document.getElementById('take-photo').style.display = 'flex';
    stopRecordButton.style.display = 'none';
    stopRecordButton.style.backgroundColor = '#e74c3c';

    try {
        const constraints = { video: { facingMode: 'environment' }, audio: true };
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        currentStream = stream;
        videoElement.srcObject = stream;
        await videoElement.play();
        cameraContainer.style.display = 'block';
        actionButtons.style.display = 'flex';
    } catch (error) {
        console.error("Error al acceder a la cámara:", error);
        alert("No se pudo acceder a la cámara. Revisa los permisos.");
        // Eliminada la referencia al botón global que causaba el error
    }
}

function takePhoto() {
    const canvas = document.getElementById('photoCanvas');
    const video = document.getElementById('videoElement');
    const context = canvas.getContext('2d');

    if (!video || !canvas) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    const dataUrl = canvas.toDataURL('image/png');
    
    if (activeItemIdx !== null) {
        // Inicializar el objeto si no existe para evitar errores de 'undefined'
        if (!window.itemMediaData) window.itemMediaData = {};
        if (!window.itemMediaData[activeItemIdx]) {
            window.itemMediaData[activeItemIdx] = { fotos: [], videos: [] };
        }
        
        window.itemMediaData[activeItemIdx].fotos.push({
            file_data: dataUrl,
            description: ""
        });

        // Refrescar miniaturas específicamente para este ítem
        renderThumbnails(activeItemIdx);
    }
}

function startVideoRecording() {
    if (!currentStream) { return; }
    try {
        let options = { mimeType: 'video/mp4; codecs=avc1' };
        if (!MediaRecorder.isTypeSupported(options.mimeType)) {
            options = { mimeType: 'video/webm' };
        }
        
        let streamToRecord = isIOS() ? new MediaStream([currentStream.getVideoTracks()[0].clone(), ...currentStream.getAudioTracks()]) : currentStream;
        
        // USAREMOS 'recordedChunks' PARA QUE COINCIDA CON TU OTRA FUNCIÓN
        window.recordedChunks = []; 
        
        videoMediaRecorder = new MediaRecorder(streamToRecord, options);
        
        // ESTA LÓGICA DE ONSTOP SE MOVIÓ A LA FUNCIÓN stopVideoRecording() 
        // PARA EVITAR DUPLICADOS Y ERRORES
        
        videoMediaRecorder.ondataavailable = event => {
            if (event.data.size > 0) window.recordedChunks.push(event.data);
        };
        
        videoMediaRecorder.start();
        updateRecordingUI(true);
    } catch (error) {
        alert('ERROR al iniciar grabación: ' + error.message);
    }
}

function stopVideoRecording() {
    if (videoMediaRecorder && videoMediaRecorder.state === 'recording') {
        
        videoMediaRecorder.onstop = () => {
            // Usamos recordedChunks (asegúrate que en startVideoRecording también se llame así)
            const videoBlob = new Blob(recordedChunks, { type: 'video/webm' });
            const reader = new FileReader();
            
            reader.onloadend = () => {
                const base64Video = reader.result;

                if (window.activeItemIdx !== null) {
                    // Inicializar el objeto si no existe
                    if (!window.itemMediaData[window.activeItemIdx]) {
                        window.itemMediaData[window.activeItemIdx] = { fotos: [], videos: [] };
                    }

                    // Guardar en el ítem activo
                    window.itemMediaData[window.activeItemIdx].videos.push({
                        file_data: base64Video,
                        description: ""
                    });

                    // FORZAR EL RENDERIZADO
                    renderThumbnails(window.activeItemIdx);
                }
            };
            reader.readAsDataURL(videoBlob);
        };

        videoMediaRecorder.stop();
    }
    updateRecordingUI(false);
}

function updateRecordingUI(isRecordingActive) {
    document.getElementById('videoElement').classList.toggle('recording-active', isRecordingActive);
    document.getElementById('start-record-btn').style.display = isRecordingActive ? 'none' : 'flex';
    document.getElementById('stop-record-btn').style.display = isRecordingActive ? 'flex' : 'none';
    document.getElementById('take-photo').style.display = isRecordingActive ? 'none' : 'flex';
}

function agregarActividadFinalizada() {
    const container = document.getElementById('container-act-finalizadas');
    const id = contadorFinalizadas++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="finalizada">
            <div class="form-group">
                <label>Ítem</label>
                <div class="input-with-icon">
                    <input type="number" class="act-item" placeholder="Número de ítem" id="fin-item-${id}">
                    <button type="button" class="record-btn" data-target-input="fin-item-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fin-item-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Descripción *</label>
                <div class="input-with-icon">
                    <input type="text" class="act-descripcion" required placeholder="Descripción de la actividad" id="fin-desc-${id}">
                    <button type="button" class="record-btn" data-target-input="fin-desc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fin-desc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observaciones</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="act-observaciones" rows="2" placeholder="Observaciones" id="fin-obs-${id}"></textarea>
                    <button type="button" class="record-btn" data-target-input="fin-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fin-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    // Agregar event listeners a los botones recién creados
    agregarListenersVoz(`fin-item-${id}`);
    agregarListenersVoz(`fin-desc-${id}`);
    agregarListenersVoz(`fin-obs-${id}`);
}

function recopilarActividadesFinalizadas() {
    const items = document.querySelectorAll('#container-act-finalizadas .actividad-item');
    const actividades = [];
    
    items.forEach((item, index) => {
        const itemNum = item.querySelector('.act-item').value || (index + 1);
        const descripcion = item.querySelector('.act-descripcion').value.trim();
        const observaciones = item.querySelector('.act-observaciones').value.trim();
        
        if (descripcion) {
            actividades.push({
                item: parseInt(itemNum),
                descripcion: descripcion,
                observaciones: observaciones
            });
        }
    });
    
    return actividades;
}

async function saveFullReport() {
    const overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.style.display = 'flex';

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const projectId = urlParams.get('project_id');
        const fechaActual = new Date().toISOString().split('T')[0];

        // 1. Equipos
        const equiposCapturados = [];
        document.querySelectorAll('#equipment-list .equipment-item span').forEach(el => {
            equiposCapturados.push(el.innerText.trim());
        });

        // 2. Notas y Multimedia (CORRECCIÓN AQUÍ)
        const notasCapturadas = [];
        const itemBoxes = document.querySelectorAll('.dynamic-item-box');
        
        itemBoxes.forEach((box) => {
            const index = box.getAttribute('data-index');
            const textoNota = box.querySelector('.field-nota').value.trim();
            
            // Accedemos al objeto correcto: window.itemMediaData
            const media = (window.itemMediaData && window.itemMediaData[index]) ? window.itemMediaData[index] : { fotos: [], videos: [] };

            // Extraemos solo el base64 de cada foto/video para que Python lo procese
            const listaFotos = media.fotos.map(f => f.file_data);
            const listaVideos = media.videos.map(v => v.file_data);

            if (textoNota || listaFotos.length > 0) {
                notasCapturadas.push({
                    texto: textoNota,
                    fotos: listaFotos,
                    videos: listaVideos
                });
            }
        });

        const reportData = {
            id_proyecto: projectId,
            fecha: fechaActual,
            equipos: equiposCapturados,
            notas: notasCapturadas
        };

        const response = await fetch('/guardar_reporte_vihesa', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reportData)
        });

        const result = await response.json();
        if (response.ok) {
            alert("✅ " + result.message);
            window.location.href = '/registros';
        } else {
            throw new Error(result.error);
        }

    } catch (error) {
        alert("❌ Error: " + error.message);
    } finally {
        if (overlay) overlay.style.display = 'none';
    }
}

function agregarActividadPendiente() {
    const container = document.getElementById('container-act-pendientes');
    const id = contadorPendientes++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="pendiente">
            <div class="form-group">
                <label>Ítem</label>
                <div class="input-with-icon">
                    <input type="number" class="act-item" placeholder="Número de ítem" id="pend-item-${id}">
                    <button type="button" class="record-btn" data-target-input="pend-item-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="pend-item-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Descripción *</label>
                <div class="input-with-icon">
                    <input type="text" class="act-descripcion" required placeholder="Descripción" id="pend-desc-${id}">
                    <button type="button" class="record-btn" data-target-input="pend-desc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="pend-desc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Pendiente generado</label>
                <div class="input-with-icon">
                    <input type="text" class="act-pendiente-generado" placeholder="Tipo de pendiente" id="pend-gen-${id}">
                    <button type="button" class="record-btn" data-target-input="pend-gen-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="pend-gen-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observaciones</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="act-observaciones" rows="2" placeholder="Observaciones" id="pend-obs-${id}"></textarea>
                    <button type="button" class="record-btn" data-target-input="pend-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="pend-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    agregarListenersVoz(`pend-item-${id}`);
    agregarListenersVoz(`pend-desc-${id}`);
    agregarListenersVoz(`pend-gen-${id}`);
    agregarListenersVoz(`pend-obs-${id}`);
}

function recopilarActividadesPendientes() {
    const items = document.querySelectorAll('#container-act-pendientes .actividad-item');
    const actividades = [];
    
    items.forEach((item, index) => {
        const itemNum = item.querySelector('.act-item').value || (index + 1);
        const descripcion = item.querySelector('.act-descripcion').value.trim();
        const pendienteGenerado = item.querySelector('.act-pendiente-generado').value.trim();
        const observaciones = item.querySelector('.act-observaciones').value.trim();
        
        if (descripcion) {
            actividades.push({
                item: parseInt(itemNum),
                descripcion: descripcion,
                pendiente_generado: pendienteGenerado,
                observaciones: observaciones
            });
        }
    });
    
    return actividades;
}

function agregarActividadFacturar() {
    const container = document.getElementById('container-act-facturar');
    const id = contadorFacturar++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="facturar">
            <div class="form-group">
                <label>Ítem</label>
                <div class="input-with-icon">
                    <input type="number" class="act-item" placeholder="Número de ítem" id="fact-item-${id}">
                    <button type="button" class="record-btn" data-target-input="fact-item-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-item-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Descripción *</label>
                <div class="input-with-icon">
                    <input type="text" class="act-descripcion" required placeholder="Descripción" id="fact-desc-${id}">
                    <button type="button" class="record-btn" data-target-input="fact-desc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-desc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Cantidad contractual</label>
                <div class="input-with-icon">
                    <input type="number" step="0.01" class="act-cant-contractual" placeholder="0.00" id="fact-cont-${id}">
                    <button type="button" class="record-btn" data-target-input="fact-cont-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-cont-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Cantidad facturada</label>
                <div class="input-with-icon">
                    <input type="number" step="0.01" class="act-cant-facturada" placeholder="0.00" id="fact-fact-${id}">
                    <button type="button" class="record-btn" data-target-input="fact-fact-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-fact-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Cantidad pendiente</label>
                <div class="input-with-icon">
                    <input type="number" step="0.01" class="act-cant-pendiente" placeholder="0.00" id="fact-pend-${id}">
                    <button type="button" class="record-btn" data-target-input="fact-pend-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-pend-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observación</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="act-observaciones" rows="2" placeholder="Observaciones" id="fact-obs-${id}"></textarea>
                    <button type="button" class="record-btn" data-target-input="fact-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="fact-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    agregarListenersVoz(`fact-item-${id}`);
    agregarListenersVoz(`fact-desc-${id}`);
    agregarListenersVoz(`fact-cont-${id}`);
    agregarListenersVoz(`fact-fact-${id}`);
    agregarListenersVoz(`fact-pend-${id}`);
    agregarListenersVoz(`fact-obs-${id}`);
}

function recopilarActividadesFacturar() {
    const items = document.querySelectorAll('#container-act-facturar .actividad-item');
    const actividades = [];
    
    items.forEach((item, index) => {
        const itemNum = item.querySelector('.act-item').value || (index + 1);
        const descripcion = item.querySelector('.act-descripcion').value.trim();
        const cantContractual = item.querySelector('.act-cant-contractual').value;
        const cantFacturada = item.querySelector('.act-cant-facturada').value;
        const cantPendiente = item.querySelector('.act-cant-pendiente').value;
        const observaciones = item.querySelector('.act-observaciones').value.trim();
        
        if (descripcion) {
            actividades.push({
                item: parseInt(itemNum),
                descripcion: descripcion,
                cantidad_contractual: parseFloat(cantContractual) || 0,
                cantidad_facturada: parseFloat(cantFacturada) || 0,
                cantidad_pendiente: parseFloat(cantPendiente) || 0,
                observacion: observaciones
            });
        }
    });
    
    return actividades;
}

function agregarDocSeguridad() {
    const container = document.getElementById('container-doc-seguridad');
    const id = contadorSeguridad++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="doc-seguridad">
            <div class="form-group">
                <label>Documento *</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-nombre" required placeholder="Nombre del documento" id="seg-doc-${id}">
                    <button type="button" class="record-btn" data-target-input="seg-doc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="seg-doc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Pendiente generado</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-pendiente" placeholder="Pendiente" id="seg-pend-${id}">
                    <button type="button" class="record-btn" data-target-input="seg-pend-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="seg-pend-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Fecha de entrega</label>
                <input type="date" class="doc-fecha">
            </div>
            <div class="form-group">
                <label>Responsable</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-responsable" placeholder="Responsable" id="seg-resp-${id}">
                    <button type="button" class="record-btn" data-target-input="seg-resp-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="seg-resp-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observaciones</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="doc-observaciones" rows="2" placeholder="Observaciones" id="seg-obs-${id}"></textarea>
                    <button type="button" class="record-btn" data-target-input="seg-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="seg-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    agregarListenersVoz(`seg-doc-${id}`);
    agregarListenersVoz(`seg-pend-${id}`);
    agregarListenersVoz(`seg-resp-${id}`);
    agregarListenersVoz(`seg-obs-${id}`);
}

function recopilarDocSeguridad() {
    const items = document.querySelectorAll('#container-doc-seguridad .actividad-item');
    const documentos = [];
    
    items.forEach(item => {
        const documento = item.querySelector('.doc-nombre').value.trim();
        const pendiente = item.querySelector('.doc-pendiente').value.trim();
        const fecha = item.querySelector('.doc-fecha').value;
        const responsable = item.querySelector('.doc-responsable').value.trim();
        const observaciones = item.querySelector('.doc-observaciones').value.trim();
        
        if (documento) {
            documentos.push({
                documento: documento,
                pendiente_generado: pendiente,
                fecha_entrega: fecha,
                responsable: responsable,
                observaciones: observaciones
            });
        }
    });
    
    return documentos;
}

function agregarDocAmbiental() {
    const container = document.getElementById('container-doc-ambiental');
    const id = contadorAmbiental++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="doc-ambiental">
            <div class="form-group">
                <label>Documento *</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-nombre" required placeholder="Nombre del documento" id="amb-doc-${id}">
                    <button type="button" class="record-btn" data-target-input="amb-doc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="amb-doc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Pendiente generado</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-pendiente" placeholder="Pendiente" id="amb-pend-${id}">
                    <button type="button" class="record-btn" data-target-input="amb-pend-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="amb-pend-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Fecha de entrega</label>
                <input type="date" class="doc-fecha">
            </div>
            <div class="form-group">
                <label>Responsable</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-responsable" placeholder="Responsable" id="amb-resp-${id}">
                    <button type="button" class="record-btn" data-target-input="amb-resp-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="amb-resp-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observaciones</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="doc-observaciones" rows="2" placeholder="Observaciones" id="amb-obs-${id}">< /textarea>
                    <button type="button" class="record-btn" data-target-input="amb-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="amb-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    agregarListenersVoz(`amb-doc-${id}`);
    agregarListenersVoz(`amb-pend-${id}`);
    agregarListenersVoz(`amb-resp-${id}`);
    agregarListenersVoz(`amb-obs-${id}`);
}

function recopilarDocAmbiental() {
    const items = document.querySelectorAll('#container-doc-ambiental .actividad-item');
    const documentos = [];
    
    items.forEach(item => {
        const documento = item.querySelector('.doc-nombre').value.trim();
        const pendiente = item.querySelector('.doc-pendiente').value.trim();
        const fecha = item.querySelector('.doc-fecha').value;
        const responsable = item.querySelector('.doc-responsable').value.trim();
        const observaciones = item.querySelector('.doc-observaciones').value.trim();
        
        if (documento) {
            documentos.push({
                documento: documento,
                pendiente_generado: pendiente,
                fecha_entrega: fecha,
                responsable: responsable,
                observaciones: observaciones
            });
        }
    });
    
    return documentos;
}

function agregarDocCalidad() {
    const container = document.getElementById('container-doc-calidad');
    const id = contadorCalidad++;
    
    const html = `
        <div class="actividad-item" data-id="${id}" data-tipo="doc-calidad">
            <div class="form-group">
                <label>Documento *</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-nombre" required placeholder="Nombre del documento" id="cal-doc-${id}">
                    <button type="button" class="record-btn" data-target-input="cal-doc-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="cal-doc-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Pendiente generado</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-pendiente" placeholder="Pendiente" id="cal-pend-${id}">
                    <button type="button" class="record-btn" data-target-input="cal-pend-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="cal-pend-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Fecha de entrega</label>
                <input type="date" class="doc-fecha">
            </div>
            <div class="form-group">
                <label>Responsable</label>
                <div class="input-with-icon">
                    <input type="text" class="doc-responsable" placeholder="Responsable" id="cal-resp-${id}">
                    <button type="button" class="record-btn" data-target-input="cal-resp-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="cal-resp-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <div class="form-group">
                <label>Observaciones</label>
                <div class="input-with-icon textarea-wrapper">
                    <textarea class="doc-observaciones" rows="2" placeholder="Observaciones" id="cal-obs-${id}"></textarea>
                    <button type="button" class="record-btn" data-target-input="cal-obs-${id}" title="Grabar">
                        <i class="fas fa-microphone"></i>
                    </button>
                    <button type="button" class="stop-btn" data-target-input="cal-obs-${id}" title="Detener" style="display: none;">
                        <i class="fas fa-stop"></i>
                    </button>
                </div>
            </div>
            <button type="button" class="remove-button" onclick="eliminarElemento(this)">
                <i class="fas fa-trash"></i> Eliminar
            </button>
        </div>
    `;
    
    container.insertAdjacentHTML('beforeend', html);
    
    agregarListenersVoz(`cal-doc-${id}`);
    agregarListenersVoz(`cal-pend-${id}`);
    agregarListenersVoz(`cal-resp-${id}`);
    agregarListenersVoz(`cal-obs-${id}`);
}

function recopilarDocCalidad() {
    const items = document.querySelectorAll('#container-doc-calidad .actividad-item');
    const documentos = [];
    
    items.forEach(item => {
        const documento = item.querySelector('.doc-nombre').value.trim();
        const pendiente = item.querySelector('.doc-pendiente').value.trim();
        const fecha = item.querySelector('.doc-fecha').value;
        const responsable = item.querySelector('.doc-responsable').value.trim();
        const observaciones = item.querySelector('.doc-observaciones').value.trim();
        
        if (documento) {
            documentos.push({
                documento: documento,
                pendiente_generado: pendiente,
                fecha_entrega: fecha,
                responsable: responsable,
                observaciones: observaciones
            });
        }
    });
    
    return documentos;
}


// ========================================
// FUNCIÓN PARA ELIMINAR ELEMENTOS
// ========================================
function eliminarElemento(button) {
    const item = button.closest('.actividad-item');
    item.remove();
}

function deletePhotoFromItem(itemIdx, photoIdx) {
    // 1. Verificar que el ítem y la foto existan en nuestro objeto de datos
    if (window.itemMediaData && window.itemMediaData[itemIdx] && window.itemMediaData[itemIdx].fotos) {
        
        // 2. Eliminar la foto específica del arreglo usando su índice
        window.itemMediaData[itemIdx].fotos.splice(photoIdx, 1);
        
        // 3. Volver a dibujar las miniaturas de ese ítem para reflejar el cambio
        renderThumbnails(itemIdx);
        
        console.log(`Foto ${photoIdx} eliminada del ítem ${itemIdx}`);
    } else {
        console.error("No se pudo encontrar la referencia para eliminar la foto.");
    }
}

// ========================================
// VALIDACIÓN DEL FORMULARIO
// ========================================
function validarFormulario() {
    // Verificar que al menos UNA sección tenga datos
    const actFinalizadas = recopilarActividadesFinalizadas();
    const actPendientes = recopilarActividadesPendientes();
    const actFacturar = recopilarActividadesFacturar();
    const docSeguridad = recopilarDocSeguridad();
    const docAmbiental = recopilarDocAmbiental();
    const docCalidad = recopilarDocCalidad();
    
    // Contar cuántas secciones tienen datos
    const seccionesConDatos = [
        actFinalizadas.length > 0,
        actPendientes.length > 0,
        actFacturar.length > 0,
        docSeguridad.length > 0,
        docAmbiental.length > 0,
        docCalidad.length > 0
    ].filter(Boolean).length;
    
    if (seccionesConDatos === 0) {
        alert('⚠️ Debes llenar al menos UNA sección del formulario:\n\n' +
              '• Actividades finalizadas\n' +
              '• Actividades pendientes\n' +
              '• Actividades por facturar\n' +
              '• Documentación de Seguridad\n' +
              '• Documentación Ambiental\n' +
              '• Documentación de Calidad');
        return false;
    }
    
    console.log(`✅ Validación OK: ${seccionesConDatos} sección(es) con datos`);
    return true;
}

// ========================================
// GUARDAR REGISTRO (ENVIAR A SYNCHRO)
// ========================================
async function saveRecordForm() {
    console.log('💾 Iniciando guardado de registro...');
    
    const button = document.getElementById('save-record-form');
    
    try {
        // 1. Validar que haya al menos UNA sección con datos
        if (!validarFormulario()) {
            console.log('⚠️ Validación falló');
            return;
        }
        
        // 2. Cambiar botón
        if (button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Guardando...';
            button.style.backgroundColor = '#ccc';
        }
        
        // 3. Recopilar TODAS las secciones
        const actFinalizadas = recopilarActividadesFinalizadas();
        const actPendientes = recopilarActividadesPendientes();
        const actFacturar = recopilarActividadesFacturar();
        const docSeguridad = recopilarDocSeguridad();
        const docAmbiental = recopilarDocAmbiental();
        const docCalidad = recopilarDocCalidad();
        const fotos = capturedPhotos.filter(f => f !== null);
        const videos = capturedVideos.filter(v => v !== null);
        
        // 4. Construir objeto con SOLO las secciones que tienen datos
        const datos = {
            // Datos básicos (siempre se envían)
            codigo_proyecto: document.getElementById('codigo_proyecto')?.value || '',
            contratista: document.getElementById('contratista')?.value || '',
            contrato: document.getElementById('contrato')?.value || '',
            fecha_registro: new Date().toISOString()
        };
        
        // Agregar solo las secciones que tienen datos
        if (actFinalizadas.length > 0) {
            datos.actividades_finalizadas = actFinalizadas;
        }
        
        if (actPendientes.length > 0) {
            datos.actividades_pendientes = actPendientes;
        }
        
        if (actFacturar.length > 0) {
            datos.actividades_facturar = actFacturar;
        }
        
        if (docSeguridad.length > 0) {
            datos.documentacion_seguridad = docSeguridad;
        }
        
        if (docAmbiental.length > 0) {
            datos.documentacion_ambiental = docAmbiental;
        }
        
        if (docCalidad.length > 0) {
            datos.documentacion_calidad = docCalidad;
        }
        
        if (fotos.length > 0) {
            datos.fotos = fotos;
        }
        
        if (videos.length > 0) {
            datos.videos = videos;
        }
        
        console.log('📦 Datos a enviar:', datos);
        console.log('📊 Secciones incluidas:', Object.keys(datos).filter(k => Array.isArray(datos[k])));
        
        // 5. Enviar al backend
        const response = await fetch('/guardar-registro', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(datos)
        });
        
        console.log('📡 Respuesta status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('❌ Error del servidor:', errorText);
            throw new Error(`Error del servidor (${response.status}): ${errorText}`);
        }
        
        const result = await response.json();
        console.log('📥 Resultado:', result);
        
        // 6. Verificar resultado
        if (result.success) {
            console.log('✅ Guardado exitoso');
            
            let mensaje = '✅ Registro guardado exitosamente!';
            
            if (result.form_id) {
                mensaje += `\n📝 Formulario Synchro: ${result.form_id}`;
            }
            
            if (result.attachments_subidos > 0) {
                mensaje += `\n📎 ${result.attachments_subidos} archivo(s) adjunto(s)`;
            }
            
            alert(mensaje);
            
            // Limpiar formulario después de 2 segundos
            setTimeout(() => {
                limpiarFormulario();
            }, 2000);
        } else {
            throw new Error(result.error || 'Error desconocido al guardar');
        }
        
    } catch (error) {
        console.error('❌ Error en saveRecordForm:', error);
        console.error('Stack trace:', error.stack);
        alert(`Error al guardar el registro:\n${error.message}`);
    } finally {
        // 7. Restaurar botón
        if (button) {
            button.disabled = false;
            button.innerHTML = '<i class="fas fa-save"></i> Guardar registro';
            button.style.backgroundColor = '#1CA3EC';
        }
    }
}

function setupVoiceButtons() {
    // Seleccionamos tanto las cajas dinámicas como los grupos de formulario estáticos
    const boxes = document.querySelectorAll('.dynamic-item-box, .form-group');
    
    boxes.forEach(box => {
        const recordBtn = box.querySelector('.record-btn');
        const stopBtn = box.querySelector('.stop-btn');
        
        if (recordBtn && stopBtn) {
            // Intentamos obtener el input de dos formas:
            // 1. Por el ID definido en data-target-input (usado en el campo global)
            // 2. Por el input que esté dentro de la misma caja (usado en ítems dinámicos)
            const targetInputId = recordBtn.getAttribute('data-target-input');
            const targetInput = targetInputId ? 
                                document.getElementById(targetInputId) : 
                                box.querySelector('input');

            if (targetInput) {
                // Limpiar eventos previos para evitar ejecuciones duplicadas
                recordBtn.onclick = null;
                stopBtn.onclick = null;

                recordBtn.onclick = function() {
                    // Feedback visual: intercambiar botones
                    recordBtn.style.display = 'none';
                    stopBtn.style.display = 'inline-block';
                    
                    // Iniciar el reconocimiento de voz pasándole el input correcto
                    if (typeof startVoiceRecognition === "function") {
                        startVoiceRecognition(targetInput, recordBtn, stopBtn);
                    }
                };

                stopBtn.onclick = function() {
                    // Detener la instancia global de reconocimiento si existe
                    if (window.currentRecognition) {
                        window.currentRecognition.stop();
                    }
                    // Restaurar botones visualmente
                    stopBtn.style.display = 'none';
                    recordBtn.style.display = 'inline-block';
                };
            }
        }
    });
}

// Función auxiliar para el reconocimiento de voz
function startVoiceRecognition(inputField, recordBtn, stopBtn) {
    const recognition = new (window.SpeechRecognition || window.webkitSpeechRecognition)();
    currentRecognition = recognition;
    
    recognition.lang = 'es-ES';
    recognition.interimResults = false;
    recognition.continuous = true; // Permite hablar por más tiempo hasta darle stop

    recognition.onresult = (event) => {
        const transcript = event.results[event.results.length - 1][0].transcript;
        inputField.value += (inputField.value ? ' ' : '') + transcript;
    };

    recognition.onend = () => {
        // Al terminar (por error o silencio largo), restauramos los botones
        stopBtn.style.display = 'none';
        recordBtn.style.display = 'inline-block';
        currentRecognition = null;
    };

    recognition.onerror = (event) => {
        console.error("Error de voz:", event.error);
        stopBtn.click(); // Forzamos el stop visual
    };

    recognition.start();
}

function mostrarMensajeExito(result) {
    const div = document.getElementById('successMessage');
    
    let mensaje = '✅ Registro guardado exitosamente en Synchro Control!';
    
    if (result.form_id) {
        mensaje += `<br>📝 Formulario ID: ${result.form_id}`;
    }
    
    if (result.attachments_subidos > 0) {
        mensaje += `<br>📎 ${result.attachments_subidos} archivos adjuntos`;
    }
    
    div.innerHTML = `<p style="color: green; font-weight: bold; padding: 15px; background: #d4edda; border-radius: 5px;">${mensaje}</p>`;
    div.style.display = 'block';
    
    setTimeout(() => {
        div.style.display = 'none';
    }, 5000);
}

function limpiarFormulario() {
    // Limpiar todos los contenedores
    document.getElementById('container-act-finalizadas').innerHTML = '';
    document.getElementById('container-act-pendientes').innerHTML = '';
    document.getElementById('container-act-facturar').innerHTML = '';
    document.getElementById('container-doc-seguridad').innerHTML = '';
    document.getElementById('container-doc-ambiental').innerHTML = '';
    document.getElementById('container-doc-calidad').innerHTML = '';
    
    // Reiniciar contadores
    contadorFinalizadas = 0;
    contadorPendientes = 0;
    contadorFacturar = 0;
    contadorSeguridad = 0;
    contadorAmbiental = 0;
    contadorCalidad = 0;
    
    // Limpiar fotos y videos
    capturedPhotos.length = 0;
    capturedVideos.length = 0;
    document.getElementById('photoThumbnails').innerHTML = '';
    document.getElementById('videoThumbnails').innerHTML = '';
    
    // Agregar una actividad finalizada por defecto
    agregarActividadFinalizada();
    
    // Ocultar cámara
    if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
        currentStream = null;
    }
    document.getElementById('camera-container').style.display = 'none';
    document.getElementById('take-photo').style.display = 'none';
    
    console.log('🧹 Formulario limpiado');
}

function renderThumbnails(idx) {
    // 1. Localizamos la tarjeta específica
    const box = document.querySelector(`.dynamic-item-box[data-index="${idx}"]`);
    if (!box) return;

    // 2. Localizamos ambos contenedores
    const photoContainer = box.querySelector('.item-photo-thumbnails');
    const videoContainer = box.querySelector('.item-video-thumbnails');
    
    // Limpiamos los contenedores antes de redibujar
    if (photoContainer) photoContainer.innerHTML = '';
    if (videoContainer) videoContainer.innerHTML = '';

    // Validamos que existan datos para este ítem
    if (!window.itemMediaData[idx]) return;

    // --- SECCIÓN DE FOTOS ---
    const fotos = window.itemMediaData[idx].fotos || [];
    fotos.forEach((foto, i) => {
        const thumbWrapper = document.createElement('div');
        thumbWrapper.className = 'photo-thumbnail-wrapper';
        const descriptionInputId = `photo_desc_item_${idx}_${i}`;

        thumbWrapper.innerHTML = `
            <img src="${foto.file_data}" class="thumbnail-image" style="width: 100px; border-radius: 8px;">
            <div class="thumbnail-description-box">
                <input type="text" id="${descriptionInputId}" class="thumbnail-input" 
                       placeholder="Descripción..." value="${foto.description || ''}"
                       onchange="window.itemMediaData[${idx}].fotos[${i}].description = this.value">
            </div>
            <div class="photo-controls">
                <button type="button" class="photo-button" onclick="deletePhotoFromItem(${idx}, ${i})">❌</button>
            </div>`;
        photoContainer.appendChild(thumbWrapper);
    });

    // --- SECCIÓN DE VIDEOS (NUEVA) ---
    const videos = window.itemMediaData[idx].videos || [];
    videos.forEach((video, i) => {
        const thumbWrapper = document.createElement('div');
        thumbWrapper.className = 'video-thumbnail-wrapper'; // Puedes crear este estilo en CSS
        const videoDescId = `video_desc_item_${idx}_${i}`;

        thumbWrapper.innerHTML = `
            <div class="video-preview-placeholder" style="width: 100px; height: 75px; background: #333; color: #fff; display: flex; align-items: center; justify-content: center; border-radius: 8px;">
                <i class="fas fa-video fa-2x"></i>
            </div>
            <div class="thumbnail-description-box">
                <input type="text" id="${videoDescId}" class="thumbnail-input" 
                       placeholder="Descripción video..." value="${video.description || ''}"
                       onchange="window.itemMediaData[${idx}].videos[${i}].description = this.value">
            </div>
            <div class="photo-controls">
                <button type="button" class="photo-button" onclick="deleteVideoFromItem(${idx}, ${i})">❌</button>
            </div>`;
        
        if (videoContainer) videoContainer.appendChild(thumbWrapper);
    });
}

function handleLocalFiles(event, idx, type) {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    // Usamos window. para asegurar que acceda a la variable global
    if (!window.itemMediaData[idx]) {
        window.itemMediaData[idx] = { fotos: [], videos: [] };
    }

    Array.from(files).forEach(file => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const base64Data = e.target.result;
            
            if (type === 'foto') {
                window.itemMediaData[idx].fotos.push({
                    file_data: base64Data,
                    description: file.name
                });
            } else {
                window.itemMediaData[idx].videos.push({
                    file_data: base64Data,
                    description: file.name
                });
            }
            // Llamamos al render para mostrar la miniatura inmediatamente
            renderThumbnails(idx);
        };
        reader.readAsDataURL(file);
    });
}


// =================================================================
//          FUNCIONES PARA ADJUNTAR ARCHIVOS
// =================================================================
function handleFileUpload(event) {
    Array.from(event.target.files).forEach(file => {
        const reader = new FileReader();
        reader.onload = (e) => {
            capturedPhotos.push(e.target.result);
            addPhotoThumbnail(e.target.result, capturedPhotos.length - 1);
        };
        reader.readAsDataURL(file);
    });
    event.target.value = '';
}

function handleVideoUpload(event) {
    const file = event.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            capturedVideos.push(e.target.result);
            addVideoThumbnail(e.target.result, capturedVideos.length - 1);
        };
        reader.readAsDataURL(file);
    }
    event.target.value = '';
}

// =================================================================
//          GRABACIÓN DE AUDIO POR CAMPO
// =================================================================
function startFieldRecording(recordButton) {
    if (isFieldRecording) return;
    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        isFieldRecording = true;
        audioFieldChunks = [];
        const targetInputId = recordButton.dataset.targetInput;
        currentTargetInput = document.getElementById(targetInputId);
        const stopButton = document.querySelector(`.stop-btn[data-target-input='${targetInputId}']`);
        recordButton.style.display = 'none';
        stopButton.style.display = 'flex';
        currentTargetInput.classList.add('recording-active');
        currentTargetInput.placeholder = "Escuchando...";
        audioMediaRecorder = new MediaRecorder(stream);
        audioMediaRecorder.start();
        audioMediaRecorder.ondataavailable = event => audioFieldChunks.push(event.data);
        audioMediaRecorder.onstop = () => {
            stream.getTracks().forEach(track => track.stop());
            const audioBlob = new Blob(audioFieldChunks, { type: 'audio/webm' });
            transcribeAudio(audioBlob);
        };
    }).catch(() => alert("No se pudo acceder al micrófono."));
}

function stopFieldRecording() {
    if (audioMediaRecorder && isFieldRecording) {
        audioMediaRecorder.stop();
    }
}

function transcribeAudio(audioBlob) {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'respuesta.webm');
    currentTargetInput.placeholder = "Transcribiendo...";
    fetch('/transcribe-audio', { method: 'POST', body: formData })
        .then(response => response.json())
        .then(data => {
            if (data.text) {
                currentTargetInput.value += (currentTargetInput.value ? ' ' : '') + data.text;
            } else {
                alert("No se pudo entender el audio.");
            }
        })
        .catch(() => alert("Error en la transcripción."))
        .finally(() => {
            const targetInputId = currentTargetInput.id;
            document.querySelector(`.record-btn[data-target-input='${targetInputId}']`).style.display = 'flex';
            document.querySelector(`.stop-btn[data-target-input='${targetInputId}']`).style.display = 'none';
            currentTargetInput.classList.remove('recording-active');
            currentTargetInput.placeholder = "";
            isFieldRecording = false;
            currentTargetInput = null;
        });
}

function addPhotoThumbnail(base64String, index) {
    const container = document.getElementById('photoThumbnails');
    const thumbWrapper = document.createElement('div');
    thumbWrapper.className = 'photo-thumbnail-wrapper';
    thumbWrapper.setAttribute('data-index', index);
    
    // Creamos un ID único para el nuevo campo de texto y sus botones
    const descriptionInputId = `photo_desc_${index}`;

    thumbWrapper.innerHTML = `
        <img src="${base64String}" class="thumbnail-image">
        
        <div class="thumbnail-description-box">
            <input type="text" id="${descriptionInputId}" class="thumbnail-input" placeholder="Describe la foto...">
            <button class="record-btn" data-target-input="${descriptionInputId}" title="Grabar descripción">
                <i class="fas fa-microphone"></i>
            </button>
            <button class="stop-btn" data-target-input="${descriptionInputId}" title="Detener grabación" style="display: none;">
                <i class="fas fa-stop"></i>
            </button>
        </div>

        <div class="photo-controls">
            <button class="photo-button" onclick="deletePhoto(${index})" title="Eliminar foto">❌</button>
        </div>`;
    
    container.appendChild(thumbWrapper);

    // IMPORTANTE: Le damos funcionalidad a los NUEVOS botones de micrófono que acabamos de crear
    const newRecordBtn = thumbWrapper.querySelector('.record-btn');
    const newStopBtn = thumbWrapper.querySelector('.stop-btn');
    newRecordBtn.addEventListener('click', () => startFieldRecording(newRecordBtn));
    newStopBtn.addEventListener('click', stopFieldRecording);
}

function deletePhoto(index) {
    capturedPhotos[index] = null;
    const thumbnailToRemove = document.querySelector(`.photo-thumbnail-wrapper[data-index='${index}']`);
    if (thumbnailToRemove) thumbnailToRemove.remove();
}

function addVideoThumbnail(base64String, index) {
    const container = document.getElementById('videoThumbnails');
    const thumbWrapper = document.createElement('div');
    thumbWrapper.className = 'photo-thumbnail-wrapper';
    thumbWrapper.setAttribute('data-video-index', index);

    // Creamos un ID único para el nuevo campo de texto y sus botones
    const descriptionInputId = `video_desc_${index}`;

    thumbWrapper.innerHTML = `
        <video src="${base64String}" class="thumbnail-image" controls playsinline></video>
        
        <div class="thumbnail-description-box">
            <input type="text" id="${descriptionInputId}" class="thumbnail-input" placeholder="Describe el video...">
            <button class="record-btn" data-target-input="${descriptionInputId}" title="Grabar descripción">
                <i class="fas fa-microphone"></i>
            </button>
            <button class="stop-btn" data-target-input="${descriptionInputId}" title="Detener grabación" style="display: none;">
                <i class="fas fa-stop"></i>
            </button>
        </div>

        <div class="photo-controls">
            <button class="photo-button" onclick="deleteVideo(${index})">❌</button>
        </div>`;

    container.appendChild(thumbWrapper);

    // IMPORTANTE: Le damos funcionalidad a los NUEVOS botones de micrófono que acabamos de crear
    const newRecordBtn = thumbWrapper.querySelector('.record-btn');
    const newStopBtn = thumbWrapper.querySelector('.stop-btn');
    newRecordBtn.addEventListener('click', () => startFieldRecording(newRecordBtn));
    newStopBtn.addEventListener('click', stopFieldRecording);
}

function deleteVideo(index) {
    capturedVideos[index] = null;
    const thumbnailToRemove = document.querySelector(`.photo-thumbnail-wrapper[data-video-index='${index}']`);
    if (thumbnailToRemove) thumbnailToRemove.remove();
}

function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
}

function saveRecord() {
    const zonaGlobal = document.getElementById('global_edificacion').value;
    if (!zonaGlobal) {
        alert("Por favor, ingrese la Edificación / Zona antes de guardar.");
        return;
    }

    const loadingOverlay = document.getElementById('loading-overlay');
    const saveButton = document.getElementById('save-record');
    loadingOverlay.style.display = 'flex';
    saveButton.disabled = true;

    const projectId = new URLSearchParams(window.location.search).get("project_id");
    const itemsReporte = [];
    const tarjetas = document.querySelectorAll('.dynamic-item-box');

    tarjetas.forEach((tarjeta) => {
        // 1. Obtenemos el índice de esta tarjeta específica
        const idx = tarjeta.getAttribute('data-index');
        
        // 2. Extraemos las fotos y videos que pertenecen SOLO a este índice
        // Buscamos en la memoria global que configuramos antes
        const fotosDelItem = (window.itemMediaData && window.itemMediaData[idx]) 
                             ? window.itemMediaData[idx].fotos : [];
        const videosDelItem = (window.itemMediaData && window.itemMediaData[idx]) 
                             ? window.itemMediaData[idx].videos : [];

        // 3. Empujamos toda la información (texto + multimedia) al ítem
        itemsReporte.push({
            item_numero: parseInt(idx) + 1,
            edificacion_zona: zonaGlobal, 
            area_inspeccionada: tarjeta.querySelector('.field-elemento').value,
            especificacion_tecnica: tarjeta.querySelector('.field-especificacion').value,
            condicion_observada: tarjeta.querySelector('.field-condicion').value,
            cumple: tarjeta.querySelector('.field-cumple').value,
            observaciones: tarjeta.querySelector('.field-observaciones').value,
            acciones_correctivas: tarjeta.querySelector('.field-acciones').value,
            // AQUÍ ESTÁ LA CLAVE: Enviamos la multimedia dentro del objeto del ítem
            fotos: fotosDelItem,
            videos: videosDelItem
        });
    });

    const payload = {
        project_id: projectId,
        items: itemsReporte
    };

    fetch('/guardar-registro', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(response => response.json())
    .then(data => {
        loadingOverlay.style.display = 'none';
        alert("¡Inspección guardada exitosamente!");
        window.location.href = '/registros';
    })
    .catch(error => {
        loadingOverlay.style.display = 'none';
        saveButton.disabled = false;
        alert(`Error: ${error.message}`);
    });
}

async function saveProject(event) {
    if (event) event.preventDefault();
    
    const form = document.getElementById('project-form');
    const formData = new FormData(form);
    const data = {};
    
    // Mapeo manual para asegurar que las llaves coincidan con lo que espera Python
    formData.forEach((value, key) => { data[key] = value; });

    try {
        const response = await fetch('/add_project', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (response.ok) {
            alert("✅ " + result.message);
            window.location.href = '/registros';
        } else {
            throw new Error(result.error || "Error desconocido");
        }
    } catch (error) {
        alert("❌ Error al guardar: " + error.message);
        console.error("Detalle del error:", error);
    }
}
