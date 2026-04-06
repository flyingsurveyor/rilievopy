/**
 * Upload helper with XHR progress reporting
 * Used across all pages (index, files, rinex, rnx2rtkp)
 */

function uploadFileWithProgress(file, opts) {
    /*
     * opts.onProgress(pct, loaded, total)  — called during upload
     * opts.onComplete(response)            — called on success
     * opts.onError(error)                  — called on failure
     * opts.url                             — endpoint (default: /api/upload)
     * opts.fieldName                       — form field name (default: 'file')
     * opts.timeout                         — ms before ontimeout fires (default: 10 min)
     */
    opts = opts || {};
    const url = opts.url || '/api/upload';
    const fieldName = opts.fieldName || 'file';
    // 10 minuti di timeout: sufficiente per file UBX/RAW grandi su connessioni lente
    const timeout = opts.timeout || 10 * 60 * 1000;

    return new Promise(function(resolve, reject) {
        var formData = new FormData();
        formData.append(fieldName, file);

        var xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.timeout = timeout;

        xhr.upload.onprogress = function(e) {
            if (e.lengthComputable) {
                var pct = Math.round((e.loaded / e.total) * 100);
                if (opts.onProgress) opts.onProgress(pct, e.loaded, e.total);
            }
        };

        xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                var data = null;
                try { data = JSON.parse(xhr.responseText); } catch(e) {}
                if (opts.onComplete) opts.onComplete(data);
                resolve(data);
            } else {
                var errMsg = xhr.responseText || ('Upload failed (' + xhr.status + ')');
                if (opts.onError) opts.onError(errMsg);
                reject(new Error(errMsg));
            }
        };

        xhr.onerror = function() {
            var msg = 'Network error during upload';
            if (opts.onError) opts.onError(msg);
            reject(new Error(msg));
        };

        xhr.ontimeout = function() {
            var msg = 'Upload timeout — file troppo grande o connessione troppo lenta';
            if (opts.onError) opts.onError(msg);
            reject(new Error(msg));
        };

        xhr.send(formData);
    });
}

/**
 * Upload multiple files sequentially with per-file progress.
 * progressEl: { wrap, bar, text } — DOM elements for progress display
 */
async function uploadFilesWithProgress(files, progressEl) {
    var wrap = progressEl.wrap;
    var bar = progressEl.bar;
    var text = progressEl.text;

    wrap.style.display = 'block';
    wrap.classList.remove('hidden');

    for (var i = 0; i < files.length; i++) {
        var f = files[i];
        var prefix = files.length > 1 ? '[' + (i+1) + '/' + files.length + '] ' : '';

        bar.style.width = '0%';
        text.textContent = prefix + 'Uploading ' + f.name + '...';

        try {
            await uploadFileWithProgress(f, {
                onProgress: function(pct) {
                    bar.style.width = pct + '%';
                    text.textContent = prefix + 'Uploading ' + f.name + ' (' + pct + '%)';
                }
            });
            bar.style.width = '100%';
            text.textContent = prefix + f.name + ' ✓';
        } catch (err) {
            text.innerHTML = '<span style="color:#e74c3c">' + prefix + f.name + ' — ' + err.message + '</span>';
            throw err;
        }
    }

    text.textContent = files.length + ' file(s) uploaded';
    return true;
}