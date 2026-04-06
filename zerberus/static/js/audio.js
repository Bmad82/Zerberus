// Hilfsfunktionen für Admin-Interface
async function playAudioFromUrl(url) {
    try {
        const audio = new Audio(url);
        await audio.play();
    } catch (error) {
        console.error('Audio playback failed:', error);
    }
}

function downloadFile(content, filename, contentType) {
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
