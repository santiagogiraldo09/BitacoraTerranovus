document.addEventListener('DOMContentLoaded', function() {
    // 1. Configuración inicial con verificación exhaustiva
    console.log('[DEBUG] Iniciando configuración de eliminación de proyectos');
    
    const deleteBtn = document.getElementById('delete-project-btn');
    const confirmModal = document.getElementById('confirmModal');
    const confirmYes = document.getElementById('confirm-yes');
    const confirmNo = document.getElementById('confirm-no');
    const urlParams = new URLSearchParams(window.location.search);
    const projectId = urlParams.get('project_id');

    // 2. Verificación de elementos (con logs detallados)
    if (!deleteBtn) console.error('[ERROR] No se encontró el botón de eliminar');
    if (!confirmModal) console.error('[ERROR] No se encontró el modal de confirmación');
    if (!projectId) console.warn('[WARNING] No se encontró project_id en la URL');

    // 3. Evento de eliminación con máxima visibilidad
    if (deleteBtn) {
        console.log('[DEBUG] Registrando evento para botón eliminar');
        
        deleteBtn.addEventListener('click', function() {
            console.log('[DEBUG] Click en botón eliminar');
            
            if (!projectId) {
                console.error('[ERROR] No hay project_id definido');
                alert('No hay proyecto seleccionado para eliminar');
                return;
            }

            if (confirmModal) {
                console.log('[DEBUG] Mostrando modal de confirmación');
                confirmModal.style.display = 'block';
            } else {
                console.error('[ERROR] No se puede mostrar el modal');
            }
        });
    }

    // 4. Confirmación con petición Fetch completa
    if (confirmYes) {
        confirmYes.addEventListener('click', async function() {
            console.log('[DEBUG] Confirmación de eliminación recibida');
            
            if (!projectId) {
                console.error('[ERROR] Intento de eliminación sin project_id');
                return;
            }

            if (confirmModal) {
                confirmModal.style.display = 'none';
            }

            console.log('[DEBUG] Enviando petición de eliminación para project_id:', projectId);
            
            try {
                const response = await fetch('/delete_project', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': '{{ csrf_token() }}'  // Importante para seguridad
                    },
                    body: JSON.stringify({
                        project_id: projectId
                    })
                });

                console.log('[DEBUG] Respuesta recibida:', response);

                const result = await response.json();
                console.log('[DEBUG] Resultado de eliminación:', result);

                if (result.success) {
                    alert(result.message);
                    console.log('[DEBUG] Redirigiendo a /registros');
                    window.location.href = "{{ url_for('registros') }}";
                } else {
                    alert('Error: ' + result.message);
                }
            } catch (error) {
                console.error('[ERROR] Fallo en la petición:', error);
                alert('Error de conexión con el servidor');
            }
        });
    }

    // 5. Cancelación de eliminación
    if (confirmNo) {
        confirmNo.addEventListener('click', function() {
            console.log('[DEBUG] Eliminación cancelada');
            if (confirmModal) {
                confirmModal.style.display = 'none';
            }
        });
    }
});