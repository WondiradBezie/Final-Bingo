// Admin Dashboard JavaScript

// State Management
let currentPage = 'dashboard';
let charts = {};
let currentUser = null;
let socket = null;

// Get auth token
function getAuthToken() {
    return localStorage.getItem('adminToken');
}

// Check auth on load
function checkAuth() {
    const token = getAuthToken();
    if (!token) {
        window.location.href = '/webapp/admin_login.html';
        return false;
    }
    return true;
}

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
    if (!checkAuth()) return;
    
    initNavigation();
    initWebSocket();
    loadDashboardData();
    updateDateTime();
    setInterval(updateDateTime, 1000);
    setInterval(refreshData, 30000); // Refresh every 30 seconds
});

// Navigation
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            
            // Update active state
            document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
            this.classList.add('active');
            
            // Get page
            const page = this.dataset.page;
            currentPage = page;
            
            // Update page title
            document.getElementById('page-title').textContent = 
                this.querySelector('span').textContent;
            
            // Show/hide pages
            document.querySelectorAll('[class$="-page"]').forEach(p => p.style.display = 'none');
            document.querySelector(`.${page}-page`).style.display = 'block';
            
            // Load page data
            loadPageData(page);
        });
    });
}

// WebSocket Connection
function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = io(`${protocol}//${window.location.host}`, {
        path: '/admin/socket.io',
        transports: ['websocket'],
        auth: { token: getAuthToken() }
    });
    
    socket.on('connect', () => {
        console.log('Admin WebSocket connected');
    });
    
    socket.on('new_game', (data) => {
        showNotification('New game started', 'info');
        if (currentPage === 'games') loadGames();
    });
    
    socket.on('new_user', (data) => {
        updateUserCount();
        showNotification(`New user joined: ${data.username}`, 'info');
        if (currentPage === 'users') loadUsers();
    });
    
    socket.on('large_transaction', (data) => {
        showNotification(`Large transaction
