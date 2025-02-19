// Update stats every 30 seconds
function updateStats() {
    fetch('/stats')
        .then(response => response.json())
        .then(data => {
            document.getElementById('active-prs').textContent = data.active_prs;
            document.getElementById('total-reviews').textContent = data.total_reviews;
        })
        .catch(error => console.error('Error fetching stats:', error));
}

// Initial update
updateStats();

// Set up periodic updates
setInterval(updateStats, 30000);
