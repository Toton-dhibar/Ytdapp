let currentDownloadId = null;
let progressInterval = null;

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('downloadForm');
    const formatType = document.getElementById('formatType');
    const getInfoBtn = document.getElementById('getInfoBtn');
    const dynamicOptions = document.getElementById('dynamicOptions');

    // Handle form submission
    form.addEventListener('submit', handleDownload);

    // Get available qualities/formats
    getInfoBtn.addEventListener('click', fetchVideoInfo);

    // Toggle dynamic options on type change
    formatType.addEventListener('change', toggleDynamicOptions);
    toggleDynamicOptions();
});

// Fetch available qualities/formats for given URL
async function fetchVideoInfo() {
    const url = document.getElementById('url').value.trim();
    const dynamicOptions = document.getElementById('dynamicOptions');
    dynamicOptions.innerHTML = '';
    dynamicOptions.classList.add('hidden');
    if (!url) {
        alert('Please enter a valid URL');
        return;
    }
    dynamicOptions.innerHTML = 'Loading...';
    dynamicOptions.classList.remove('hidden');
    try {
        const response = await fetch('/info', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ url })
        });
        const data = await response.json();
        if (response.ok) {
            let html = '';
            // For video
            html += `
            <div id="videoDynamic" style="display:none;">
                <label for="dyn_video_quality">Video Quality:</label>
                <select id="dyn_video_quality" name="dyn_video_quality">
                    ${data.video_qualities.map(v =>
                        `<option value="${v.format_id}">${v.quality} ${v.ext} ${v.fps ? v.fps+'fps' : ''}</option>`
                    ).join('')}
                </select>
                <label for="dyn_video_format">Video Format:</label>
                <select id="dyn_video_format" name="dyn_video_format">
                    ${data.video_formats.map(f =>
                        `<option value="${f}">${f.toUpperCase()}</option>`
                    ).join('')}
                </select>
            </div>
            `;
            // For audio
            html += `
            <div id="audioDynamic" style="display:none;">
                <label for="dyn_audio_quality">Audio Quality:</label>
                <select id="dyn_audio_quality" name="dyn_audio_quality">
                    ${data.audio_qualities.map(a =>
                        `<option value="${a.format_id}">${a.abr} ${a.ext}</option>`
                    ).join('')}
                </select>
                <label for="dyn_audio_format">Audio Format:</label>
                <select id="dyn_audio_format" name="dyn_audio_format">
                    ${data.audio_formats.map(f =>
                        `<option value="${f}">${f.toUpperCase()}</option>`
                    ).join('')}
                </select>
            </div>
            `;
            dynamicOptions.innerHTML = html;
            dynamicOptions.classList.remove('hidden');
            // Initial show/hide depending on Download Type
            toggleDynamicOptions();
        } else {
            dynamicOptions.innerHTML = `<div style="color:red;">${data.error || 'Error fetching info'}</div>`;
        }
    } catch (err) {
        dynamicOptions.innerHTML = `<div style="color:red;">Error: ${err.message}</div>`;
    }
}

// Show/hide dynamic video/audio options based on Download Type
function toggleDynamicOptions() {
    const formatType = document.getElementById('formatType').value;
    const videoDynamic = document.getElementById('videoDynamic');
    const audioDynamic = document.getElementById('audioDynamic');
    if (videoDynamic) videoDynamic.style.display = formatType === 'video' ? '' : 'none';
    if (audioDynamic) audioDynamic.style.display = formatType === 'audio' ? '' : 'none';
}

async function handleDownload(e) {
    e.preventDefault();
    resetDownloadState();
    const url = document.getElementById('url').value.trim();
    const formatType = document.getElementById('formatType').value;
    let format_id, format_ext;
    if (formatType === 'video') {
        format_id = document.getElementById('dyn_video_quality')?.value;
        format_ext = document.getElementById('dyn_video_format')?.value;
    } else {
        format_id = document.getElementById('dyn_audio_quality')?.value;
        format_ext = document.getElementById('dyn_audio_format')?.value;
    }
    if (!url || !format_id || !format_ext) {
        showError('Please select all options and enter a valid URL.');
        return;
    }
    const downloadData = {
        url,
        format_type: formatType,
        format_id,
        format: format_ext
    };
    setDownloadState(true);
    showProgressSection();
    try {
        const response = await fetch('/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(downloadData)
        });
        const result = await response.json();
        if (response.ok) {
            currentDownloadId = result.download_id;
            showSuccess('Download started successfully!');
            startProgressTracking();
        } else {
            throw new Error(result.error);
        }
    } catch (error) {
        showError('Error starting download: ' + error.message);
        setDownloadState(false);
        hideProgressSection();
        resetDownloadState();
    }
}

function resetDownloadState() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
    currentDownloadId = null;
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const speedText = document.getElementById('speedText');
    const downloadStatus = document.getElementById('downloadStatus');
    if (progressFill) progressFill.style.width = '0%';
    if (progressText) progressText.textContent = '0%';
    if (speedText) speedText.textContent = '';
    if (downloadStatus) {
        downloadStatus.textContent = '';
        downloadStatus.className = '';
    }
}

function startProgressTracking() {
    if (progressInterval) {
        clearInterval(progressInterval);
    }
    progressInterval = setInterval(async () => {
        if (!currentDownloadId) {
            clearInterval(progressInterval);
            return;
        }
        try {
            const response = await fetch(`/progress/${currentDownloadId}`);
            const progress = await response.json();
            updateProgress(progress);
            if (progress.status === 'finished') {
                clearInterval(progressInterval);
                setDownloadState(false);
                triggerFileDownload();
            } else if (progress.status === 'error') {
                clearInterval(progressInterval);
                setDownloadState(false);
                showError('Download failed: ' + progress.error);
                setTimeout(() => {
                    hideProgressSection();
                    resetDownloadState();
                }, 5000);
            }
        } catch (error) {
            console.error('Error tracking progress:', error);
        }
    }, 1000);
}

function updateProgress(progress) {
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const speedText = document.getElementById('speedText');
    const downloadStatus = document.getElementById('downloadStatus');
    if (!progressFill || !progressText || !speedText || !downloadStatus) return;
    if (progress.status === 'downloading') {
        const percent = progress.percent || '0%';
        progressFill.style.width = percent;
        progressText.textContent = percent;
        speedText.textContent = progress.speed || '';
        downloadStatus.textContent = 'Downloading...';
        downloadStatus.className = '';
    } else if (progress.status === 'finished') {
        progressFill.style.width = '100%';
        progressText.textContent = '100%';
        speedText.textContent = '';
        downloadStatus.textContent = 'Download completed! Preparing your file...';
        downloadStatus.className = 'success';
    } else if (progress.status === 'error') {
        downloadStatus.textContent = 'Download failed: ' + (progress.error || 'Unknown error');
        downloadStatus.className = 'error';
    }
}

function triggerFileDownload() {
    if (!currentDownloadId) return;
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = `/download_file/${currentDownloadId}`;
    iframe.onload = function() {
        setTimeout(() => {
            showSuccess('Download completed! Your file should start downloading shortly.');
            setTimeout(() => {
                hideProgressSection();
                resetDownloadState();
                if (iframe.parentNode) {
                    iframe.parentNode.removeChild(iframe);
                }
            }, 3000);
        }, 1000);
    };
    iframe.onerror = function() {
        showError('Error downloading file. Please try again.');
        hideProgressSection();
        resetDownloadState();
        if (iframe.parentNode) {
            iframe.parentNode.removeChild(iframe);
        }
    };
    document.body.appendChild(iframe);
}

function setDownloadState(downloading) {
    const downloadBtn = document.getElementById('downloadBtn');
    if (!downloadBtn) return;
    const btnText = downloadBtn.querySelector('.btn-text');
    const btnLoading = downloadBtn.querySelector('.btn-loading');
    downloadBtn.disabled = downloading;
    if (downloading) {
        if (btnText) btnText.classList.add('hidden');
        if (btnLoading) btnLoading.classList.remove('hidden');
    } else {
        if (btnText) btnText.classList.remove('hidden');
        if (btnLoading) btnLoading.classList.add('hidden');
    }
}

function showProgressSection() {
    const progressSection = document.getElementById('progressSection');
    if (progressSection) {
        progressSection.classList.remove('hidden');
    }
    resetDownloadState();
}

function hideProgressSection() {
    const progressSection = document.getElementById('progressSection');
    if (progressSection) {
        progressSection.classList.add('hidden');
    }
}

function showError(message) {
    removeExistingMessages();
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.textContent = message;
    const form = document.querySelector('.download-form');
    if (form) {
        form.appendChild(errorDiv);
        setTimeout(() => {
            if (errorDiv.parentNode === form) {
                errorDiv.remove();
            }
        }, 5000);
    }
}

function showSuccess(message) {
    removeExistingMessages();
    const successDiv = document.createElement('div');
    successDiv.className = 'success-message';
    successDiv.textContent = message;
    const form = document.querySelector('.download-form');
    if (form) {
        form.appendChild(successDiv);
        setTimeout(() => {
            if (successDiv.parentNode === form) {
                successDiv.remove();
            }
        }, 5000);
    }
}

function removeExistingMessages() {
    const existing = document.querySelectorAll('.error-message, .success-message');
    existing.forEach(el => {
        if (el.parentNode) {
            el.remove();
        }
    });
}

// Reset form button logic (used in complete section)
function resetForm() {
    resetDownloadState();
    document.getElementById('url').value = '';
    document.getElementById('formatType').value = 'video';
    document.getElementById('dynamicOptions').innerHTML = '';
    document.getElementById('dynamicOptions').classList.add('hidden');
    document.getElementById('fetchingProgressFill').style.width = '0%';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressText').textContent = '0%';
    document.getElementById('speedText').textContent = '';
    document.getElementById('fetchingStatus').textContent = '';
    document.getElementById('downloadStatus').textContent = '';
    document.getElementById('downloadLink').innerHTML = '';
    hideAllProgress();
    document.getElementById('completeSection').classList.add('hidden');
    document.getElementById('downloadForm').classList.remove('hidden');
    document.getElementById('downloadBtn').classList.remove('loading');
    toggleDynamicOptions();
}

// Progress indicator logic
function showFetchingProgress() {
    document.getElementById('fetchingSection').classList.remove('hidden');
    document.getElementById('progressSection').classList.add('hidden');
    document.getElementById('completeSection').classList.add('hidden');
}
function hideAllProgress() {
    document.getElementById('fetchingSection').classList.add('hidden');
    document.getElementById('progressSection').classList.add('hidden');
    document.getElementById('completeSection').classList.add('hidden');
}
