// ==================================================================
// VARIABLES GLOBALES Y CONFIGURACIÓN
// ==================================================================
let currentStream = null;
const capturedPhotos = []; // Array para fotos en base64
const capturedVideos = []; // Array para videos en base64
let videoMediaRecorder;
let videoChunks = [];

// Variable para rastrear qué campo de texto está grabando
let activeMicButton = null;
let activeInputId = null;

// IDs únicos para campos dinámicos
let dynamicFieldCounter = 0;

// Variables para la grabación de audio por campo
let audioMediaRecorder;
let audioFieldChunks = [];
let isFieldRecording = false;
let currentTargetInput = null;


// ==================================================================
// INICIALIZACIÓN DEL FORMULARIO
// ==================================================================
document.addEventListener('DOMContentLoaded', () => {
    // 1. Cargar datos iniciales del proyecto (Código, Contratista, etc.)
    loadProjectData();

    // 2. Configurar listeners para botones "+ Agregar"
    document.querySelectorAll('.add-item-btn').forEach(button => {
        button.addEventListener('click', () => {
            const templateId = button.dataset.template;
            const containerId = button.parentElement.id;
            addDynamicField(templateId, containerId);
        });
    });

    // 3. Configurar listener para el botón "Guardar"
    document.getElementById('save-form-button').addEventListener('click', saveFormToSynchro);
});

/**
 * Carga los datos iniciales del formulario (Código, Contratista, Contrato)
 * Esto debe llamar a un nuevo endpoint en tu app.py que use la API de Synchro.
 */
async function loadProjectData() {
    console.log("Cargando datos del proyecto desde Synchro...");
    
    // ==================================================================
    // ¡¡¡IMPORTANTE!!!
    // Cambia este número por el del formulario que quieres editar
    // ==================================================================
    const formNumber = "2.02-00003"; // <--- CAMBIA ESTO

    try {
        const response = await fetch(`/get-synchro-form-data?form_number=${formNumber}`);
        
        if (!response.ok) {
            throw new Error('No se pudo cargar la información del formulario desde el backend.');
        }

        const data = await response.json();

        // ---- INICIO DE LA MODIFICACIÓN ----
        // Leemos los nombres de propiedad del nuevo JSON
        
        // El 'number' (ej: 2.02-00003) viene del objeto principal
        // const formCodigo = data.number || formNumber; 
        
        // Los otros campos vienen de 'properties'
        const props = data.properties || {};
        
        // Usamos los nombres del JSON: 'Codigo Proyecto', 'Contrato', 'Contratista'
        document.getElementById('form-codigo-proyecto').value = props['Codigo Proyecto'] || 'N/A';
        document.getElementById('form-contratista').value = props.Contratista || 'N/A';
        document.getElementById('form-contrato').value = props.Contrato || 'N/A';
        
        // ---- FIN DE LA MODIFICACIÓN ----

    } catch (error)
    {
        console.error("Error al cargar datos del proyecto:", error);
        alert("Error al cargar datos del proyecto. Revisa la consola y el backend.");
    }
}


// ==================================================================
// LÓGICA DE CAMPOS DINÁMICOS
// ==================================================================

/**
 * Clona una plantilla y la agrega a un contenedor
 * @param {string} templateId - ID de la etiqueta <template>
 * @param {string} containerId - ID del div contenedor donde se agregará
 */
function addDynamicField(templateId, containerId) {
    const template = document.getElementById(templateId);
    const container = document.getElementById(containerId);
    
    if (!template || !container) {
        console.error("No se encontró la plantilla o el contenedor", templateId, containerId);
        return;
    }

    const clone = template.content.firstElementChild.cloneNode(true);

    // --- INICIO DE LA MODIFICACIÓN ---
    
    // 1. Asignar IDs únicos a los inputs Y asignarles los data-target a sus botones
    clone.querySelectorAll('.form-input').forEach(input => {
        // Solo procesamos inputs que no sean <select>
        if (input.tagName === 'SELECT') return;

        const newId = `dynamic-input-${dynamicFieldCounter++}`;
        input.id = newId;

        // Buscar los botones que están al mismo nivel (hermanos)
        const buttonContainer = input.closest('.input-with-icon');
        if (buttonContainer) {
            const recordBtn = buttonContainer.querySelector('.record-btn');
            const stopBtn = buttonContainer.querySelector('.stop-btn');
            
            // Asignar el ID del input al data-target de los botones
            if (recordBtn) recordBtn.dataset.targetInput = newId;
            if (stopBtn) stopBtn.dataset.targetInput = newId;
        }
    });

    // 2. Asignar listeners a los nuevos botones de micrófono
    clone.querySelectorAll('.record-btn').forEach(button => {
        button.addEventListener('click', () => startFieldRecording(button));
    });

    // 3. Asignar listeners a los nuevos botones de stop
    clone.querySelectorAll('.stop-btn').forEach(button => {
        button.addEventListener('click', stopFieldRecording);
    });

    // --- FIN DE LA MODIFICACIÓN ---

    // Asignar listener al botón de eliminar (esto ya lo tenías)
    clone.querySelector('.delete-item-btn').addEventListener('click', () => {
        clone.remove();
    });

    container.appendChild(clone);
}


// =================================================================
//          GRABACIÓN DE AUDIO POR CAMPO (Lógica Original)
// =================================================================
function startFieldRecording(recordButton) {
    if (isFieldRecording) return;
    
    const targetInputId = recordButton.dataset.targetInput;
    currentTargetInput = document.getElementById(targetInputId);
    if (!currentTargetInput) {
        console.error("No se encontró el input target:", targetInputId);
        return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
        isFieldRecording = true;
        audioFieldChunks = [];
        
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
    }).catch((err) => {
        console.error("Error al acceder al micrófono:", err);
        alert("No se pudo acceder al micrófono.");
    });
}

function stopFieldRecording() {
    if (audioMediaRecorder && isFieldRecording) {
        audioMediaRecorder.stop();
    }
}

function transcribeAudio(audioBlob) {
    if (!currentTargetInput) return;

    const formData = new FormData();
    formData.append('audio', audioBlob, 'respuesta.webm');
    currentTargetInput.placeholder = "Transcribiendo...";

    fetch('/transcribe-audio', { method: 'POST', body: formData })
        .then(response => response.json())
        .then(data => {
            if (data.text) {
                
                // --- INICIO DE LA CORRECCIÓN ---
                let transcript = data.text;

                // Verificamos si el campo de destino es de tipo "number"
                if (currentTargetInput.type === 'number') {
                    
                    // 1. Quitamos espacios, comas o cualquier cosa que no sea un dígito o un punto.
                    transcript = transcript.replace(/[^\d.]/g, '');
                    
                    // 2. Quitamos el punto decimal SOLO SI está al final del número (ej: "1." se vuelve "1", pero "5.5" se queda).
                    transcript = transcript.replace(/\.$/, '');
                }
                
                // Asignamos el texto ya limpio
                currentTargetInput.value = transcript; 
                // --- FIN DE LA CORRECCIÓN ---

            } else {
                alert("No se pudo entender el audio.");
            }
        })
        .catch((err) => {
            console.error("Error en transcripción:", err);
            alert("Error en la transcripción.");
        })
        .finally(() => {
            const targetInputId = currentTargetInput.id;
            // Usamos querySelector para asegurarnos de encontrar los botones
            const recordBtn = document.querySelector(`.record-btn[data-target-input='${targetInputId}']`);
            const stopBtn = document.querySelector(`.stop-btn[data-target-input='${targetInputId}']`);
            
            if (recordBtn) recordBtn.style.display = 'flex';
            if (stopBtn) stopBtn.style.display = 'none';

            currentTargetInput.classList.remove('recording-active');
            currentTargetInput.placeholder = ""; // Limpiar placeholder
            isFieldRecording = false;
            currentTargetInput = null;
        });
}


// ==================================================================
// LÓGICA DE CÁMARA Y ARCHIVOS (Copiada de script.js)
// ==================================================================

document.getElementById('start-camera').addEventListener('click', () => {
    startCamera("environment");
    document.getElementById('start-camera').style.display = 'none';
    document.getElementById('camera-controls').style.display = 'block';
});

function startCamera(facingMode = "environment") {
    const video = document.getElementById('videoElement');
    const cameraContainer = document.getElementById('camera-container');

    if (currentStream) {
        currentStream.getTracks().forEach(track => track.stop());
    }

    navigator.mediaDevices.getUserMedia({
        video: { facingMode: { exact: facingMode } }
    }).then(stream => {
        currentStream = stream;
        video.srcObject = stream;
        video.play();
        cameraContainer.style.display = 'block';
    }).catch(error => {
        console.warn(`Falló modo ${facingMode}, intentando default`, error);
        navigator.mediaDevices.getUserMedia({ video: true }).then(fallbackStream => {
            currentStream = fallbackStream;
            video.srcObject = fallbackStream;
            video.play();
            cameraContainer.style.display = 'block';
        }).catch(fallbackError => {
            console.error("No se pudo acceder a ninguna cámara.", fallbackError);
            alert("No se pudo acceder a la cámara.");
        });
    });
}

// Tomar foto
document.getElementById('take-photo').addEventListener('click', () => {
    const canvas = document.getElementById('photoCanvas');
    const video = document.getElementById('videoElement');
    if (video.readyState !== 4) return;

    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    
    const fotoBase64 = canvas.toDataURL('image/jpeg', 0.7);
    capturedPhotos.push(fotoBase64);
    addPhotoThumbnail(fotoBase64, capturedPhotos.length - 1);
});

// Grabar video
document.getElementById('start-record-btn').addEventListener('click', () => {
    if (!currentStream) {
        alert("La cámara no está activa.");
        return;
    }
    videoChunks = [];
    videoMediaRecorder = new MediaRecorder(currentStream, { mimeType: 'video/webm' });

    videoMediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) videoChunks.push(event.data);
    };

    videoMediaRecorder.onstop = () => {
        const videoBlob = new Blob(videoChunks, { type: 'video/webm' });
        const reader = new FileReader();
        reader.readAsDataURL(videoBlob);
        reader.onloadend = () => {
            const videoBase64 = reader.result;
            capturedVideos.push(videoBase64);
            addVideoThumbnail(videoBase64, capturedVideos.length - 1);
        };
    };

    videoMediaRecorder.start();
    document.getElementById('start-record-btn').style.display = 'none';
    document.getElementById('stop-record-btn').style.display = 'inline-block';
});

// Detener video
document.getElementById('stop-record-btn').addEventListener('click', () => {
    if (videoMediaRecorder && videoMediaRecorder.state === 'recording') {
        videoMediaRecorder.stop();
    }
    document.getElementById('start-record-btn').style.display = 'inline-block';
    document.getElementById('stop-record-btn').style.display = 'none';
});

// Adjuntar foto
document.getElementById('file-input').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const base64 = e.target.result;
            capturedPhotos.push(base64);
            addPhotoThumbnail(base64, capturedPhotos.length - 1);
        };
        reader.readAsDataURL(file);
    }
    event.target.value = ''; // Reset
});

// Adjuntar video
document.getElementById('video-file-input').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const videoBase64 = e.target.result;
            capturedVideos.push(videoBase64);
            addVideoThumbnail(videoBase64, capturedVideos.length - 1);
        };
        reader.readAsDataURL(file);
    }
    event.target.value = ''; // Reset
});

// Funciones de miniaturas (thumbnails)
function addPhotoThumbnail(base64String, index) {
    const container = document.getElementById('photoThumbnails');
    const wrapper = document.createElement('div');
    wrapper.className = 'photo-thumbnail-wrapper';
    wrapper.setAttribute('data-index', index);
    wrapper.innerHTML = `
        <img src="${base64String}" class="thumbnail-image">
        <button class="delete-media-btn" onclick="deletePhoto(${index})">&times;</button>
    `;
    container.appendChild(wrapper);
}

function addVideoThumbnail(base64String, index) {
    const container = document.getElementById('videoThumbnails');
    const wrapper = document.createElement('div');
    wrapper.className = 'photo-thumbnail-wrapper';
    wrapper.setAttribute('data-index', index);
    wrapper.innerHTML = `
        <video src="${base64String}" class="thumbnail-image" controls></video>
        <button class="delete-media-btn" onclick="deleteVideo(${index})">&times;</button>
    `;
    container.appendChild(wrapper);
}

function deletePhoto(index) {
    capturedPhotos[index] = null; // Marcar como nulo
    const thumbnail = document.querySelector(`#photoThumbnails .photo-thumbnail-wrapper[data-index='${index}']`);
    if (thumbnail) thumbnail.remove();
}

function deleteVideo(index) {
    capturedVideos[index] = null; // Marcar como nulo
    const thumbnail = document.querySelector(`#videoThumbnails .photo-thumbnail-wrapper[data-index='${index}']`);
    if (thumbnail) thumbnail.remove();
}

// ==================================================================
// LÓGICA DE GUARDADO EN SYNCHRO
// ==================================================================

/**
 * Recolecta TODOS los datos del formulario y los envía al backend
 * para actualizar Synchro Control.
 */
async function saveFormToSynchro() {
    console.log("Iniciando guardado en Synchro...");
    const saveButton = document.getElementById('save-form-button');
    saveButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Actualizando...'; // Muestra un spinner
    saveButton.disabled = true; // Deshabilita el botón para evitar doble clic
    const properties = {};
    const sections = document.querySelectorAll('.accordion-content[data-api-section]');

    // Obtenemos el número de formulario desde el campo de texto (que se cargó en loadProjectData)
    // Usamos 'form-codigo-proyecto' y tomamos el 'number' del formulario, no el 'Codigo Proyecto'
    const formNumberInput = document.getElementById('form-codigo-proyecto');
    // Esto es un truco: guardamos el n° de formulario real en un atributo data-*
    const formNumber = formNumberInput.dataset.formNumber || "2.02-00003"; // Fallback


    sections.forEach(section => {
        const sectionName = section.dataset.apiSection; // Ej: "Cumplimientos"
        const items = [];
        
        section.querySelectorAll('.dynamic-item-box').forEach(itemBox => {
            const itemData = {
                'id': crypto.randomUUID() 
            };
            
            
            itemBox.querySelectorAll('.form-input').forEach(input => {
                const apiName = input.dataset.apiName;
                if (!apiName) return;

                let value = input.value; // Obtenemos el valor

                // Si el input está vacío, enviar null
                if (value === '') {
                    itemData[apiName] = null;
                }
                // Si es un campo de fecha, formatearlo a ISO
                else if (input.type === 'date' && apiName === 'Fecha__x0020__Arribo') {
                    // input.value = "2025-10-30"
                    // API espera = "2025-10-30T00:00:00Z"
                    itemData[apiName] = `${value}T00:00:00Z`;
                }
                // Si es de tipo número, convertirlo a número
                else if (input.type === 'number') {
                    // Usamos parseFloat para admitir decimales (ej: Cantidad 5.0)
                    itemData[apiName] = parseFloat(value);
                    if (isNaN(itemData[apiName])) { // Si la conversión falla
                        itemData[apiName] = null;
                    }
                } 
                // Los campos de texto y <select> se quedan como string
                else {
                    itemData[apiName] = value;
                }
            });
            // ---- FIN DE LA MODIFICACIÓN ----

            items.push(itemData);
        });

        properties[sectionName] = items;
    });
    
    // NOTA: 'media' (fotos/videos) se ha eliminado de este payload
    // Si lo necesitas, vuelve a agregarlo.
    const payload = {
        form_number: formNumber,
        properties: properties
    };

    console.log("Payload a enviar al backend:", JSON.stringify(payload, null, 2));

    try {
        const response = await fetch('/update-synchro-form', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Error desconocido al guardar en Synchro.");
        }

        alert("¡Formulario guardado en Synchro exitosamente!");
        console.log("Respuesta de Synchro:", result);
        window.location.reload(); 

    } catch (error) {
        console.error("Error al guardar en Synchro:", error);
        alert(`Error al guardar: ${error.message}`);

        saveButton.innerHTML = '<i class="fas fa-save"></i> Guardar Formulario';
        saveButton.disabled = false;
    }
}