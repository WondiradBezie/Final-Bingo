// Admin Dashboard JavaScript

// State Management
let currentPage = 'dashboard';
let charts = {};
let currentUser = null;
let socket = null;

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
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
        transports: ['websocket']
    });
    
    socket.on('connect', () => {
        console.log('Admin WebSocket connected');
        socket.emit('admin_authenticate', { token: localStorage.getItem('adminToken') });
    });
    
    socket.on('new_game', (data) => {
        showNotification('New game started', 'info');
        if (currentPage === 'games') loadGames();
    });
    
    socket.on('new_user', (data) => {
        updateUserCount();
        showNotification(`New user joined: ${data.username}`, 'info');
    });
    
    socket.on('large_transaction', (data) => {
        showNotification(`Large transaction: ${data.amount} Birr`, 'warning');
        if (currentPage === 'transactions') loadTransactions();
    });
    
    socket.on('system_alert', (data) => {
        showNotification(data.message, 'critical');
    });
}

// Dashboard Data
async function loadDashboardData() {
    try {
        const response = await fetch('/api/admin/dashboard');
        const data = await response.json();
        
        // Update stats
        document.getElementById('total-users').textContent = data.totalUsers;
        document.getElementById('active-games').textContent = data.activeGames;
        document.getElementById('total-volume').textContent = formatCurrency(data.totalVolume);
        document.getElementById('total-commission').textContent = formatCurrency(data.totalCommission);
        
        // Update changes
        document.getElementById('user-change').textContent = `${data.userChange > 0 ? '+' : ''}${data.userChange}%`;
        document.getElementById('volume-change').textContent = `${data.volumeChange > 0 ? '+' : ''}${data.volumeChange}%`;
        
        // Create charts
        createRevenueChart(data.revenue);
        createGamesChart(data.gamesHistory);
        
        // Load recent activity
        loadRecentActivity();
        
    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

// Charts
function createRevenueChart(data) {
    const ctx = document.getElementById('revenue-chart').getContext('2d');
    
    if (charts.revenue) charts.revenue.destroy();
    
    charts.revenue = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Revenue',
                data: data.values,
                borderColor: '#6366f1',
                backgroundColor: 'rgba(99, 102, 241, 0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: value => formatCurrency(value)
                    }
                }
            }
        }
    });
}

function createGamesChart(data) {
    const ctx = document.getElementById('games-chart').getContext('2d');
    
    if (charts.games) charts.games.destroy();
    
    charts.games = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Active Games',
                data: data.values,
                backgroundColor: '#8b5cf6',
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            }
        }
    });
}

// Recent Activity
async function loadRecentActivity() {
    try {
        const response = await fetch('/api/admin/recent-activity');
        const activities = await response.json();
        
        const container = document.getElementById('recent-activity');
        container.innerHTML = '';
        
        activities.forEach(activity => {
            const row = document.createElement('div');
            row.className = 'activity-row';
            row.innerHTML = `
                <div class="activity-time">${formatTime(activity.time)}</div>
                <div class="activity-type ${activity.type}">${activity.type}</div>
                <div class="activity-user">${activity.user}</div>
                <div class="activity-details">${activity.details}</div>
            `;
            container.appendChild(row);
        });
        
    } catch (error) {
        console.error('Error loading activity:', error);
    }
}

// Games Management
async function loadGames() {
    try {
        const search = document.getElementById('game-search')?.value || '';
        const status = document.getElementById('game-status-filter')?.value || 'all';
        const room = document.getElementById('game-room-filter')?.value || 'all';
        
        const response = await fetch(`/api/admin/games?search=${search}&status=${status}&room=${room}`);
        const games = await response.json();
        
        const tbody = document.getElementById('games-list');
        tbody.innerHTML = '';
        
        games.forEach(game => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${game.game_id}</td>
                <td>${game.room}</td>
                <td><span class="status-badge status-${game.status}">${game.status}</span></td>
                <td>${game.players}/${game.max_players}</td>
                <td>${formatCurrency(game.prize_pool)}</td>
                <td>${formatDuration(game.duration)}</td>
                <td>${game.winners || '-'}</td>
                <td>
                    <button class="action-btn view" onclick="viewGame('${game.game_id}')">
                        <i class="fas fa-eye"></i>
                    </button>
                    <button class="action-btn edit" onclick="editGame('${game.game_id}')">
                        <i class="fas fa-edit"></i>
                    </button>
                    <button class="action-btn ban" onclick="endGame('${game.game_id}')">
                        <i class="fas fa-stop"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
        
    } catch (error) {
        console.error('Error loading games:', error);
    }
}

// Users Management
async function loadUsers() {
    try {
        const search = document.getElementById('user-search')?.value || '';
        const status = document.getElementById('user-status-filter')?.value || 'all';
        const sort = document.getElementById('user-sort')?.value || 'balance_desc';
        
        const response = await fetch(`/api/admin/users?search=${search}&status=${status}&sort=${sort}`);
        const users = await response.json();
        
        // Update stats
        document.getElementById('user-total').textContent = users.total;
        document.getElementById('user-active-today').textContent = users.activeToday;
        document.getElementById('user-new-today').textContent = users.newToday;
        document.getElementById('user-total-balance').textContent = formatCurrency(users.totalBalance);
        
        const tbody = document.getElementById('users-list');
        tbody.innerHTML = '';
        
        users.list.forEach(user => {
            const winRate = user.games_played > 0 
                ? ((user.games_won / user.games_played) * 100).toFixed(1) 
                : 0;
            
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${user.id}</td>
                <td>@${user.username || 'N/A'}</td>
                <td><strong>${formatCurrency(user.balance)}</strong></td>
                <td>${user.games_played}</td>
                <td>${user.games_won}</td>
                <td>${winRate}%</td>
                <td>
                    <span class="status-badge ${user.is_banned ? 'status-banned' : 'status-active'}">
                        ${user.is_banned ? 'Banned' : 'Active'}
                    </span>
                    ${user.is_vip ? '<span class="status-badge status-vip">VIP</span>' : ''}
                </td>
                <td>${formatTimeAgo(user.last_seen)}</td>
                <td>
                    <button class="action-btn view" onclick="viewUser('${user.id}')">
                        <i class="fas fa-eye"></i>
                    </button>
                    <button class="action-btn edit" onclick="adjustUserBalance('${user.id}')">
                        <i class="fas fa-coins"></i>
                    </button>
                    <button class="action-btn ban" onclick="toggleUserBan('${user.id}')">
                        <i class="fas fa-ban"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
        
    } catch (error) {
        console.error('Error loading users:', error);
    }
}

// Transactions
async function loadTransactions() {
    try {
        const search = document.getElementById('tx-search')?.value || '';
        const type = document.getElementById('tx-type-filter')?.value || 'all';
        const from = document.getElementById('tx-date-from')?.value || '';
        const to = document.getElementById('tx-date-to')?.value || '';
        
        const response = await fetch(`/api/admin/transactions?search=${search}&type=${type}&from=${from}&to=${to}`);
        const data = await response.json();
        
        // Update stats
        document.getElementById('today-deposits').textContent = formatCurrency(data.todayDeposits);
        document.getElementById('today-withdrawals').textContent = formatCurrency(data.todayWithdrawals);
        document.getElementById('today-wins').textContent = formatCurrency(data.todayWins);
        document.getElementById('net-revenue').textContent = formatCurrency(data.netRevenue);
        
        const tbody = document.getElementById('transactions-list');
        tbody.innerHTML = '';
        
        data.transactions.forEach(tx => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${formatTime(tx.time)}</td>
                <td>${tx.user}</td>
                <td><span class="status-badge status-${tx.type}">${tx.type}</span></td>
                <td class="${tx.amount > 0 ? 'positive' : 'negative'}">
                    ${tx.amount > 0 ? '+' : ''}${formatCurrency(tx.amount)}
                </td>
                <td>${formatCurrency(tx.balance_after)}</td>
                <td>${tx.reference}</td>
                <td><span class="status-badge status-${tx.status}">${tx.status}</span></td>
            `;
            tbody.appendChild(row);
        });
        
    } catch (error) {
        console.error('Error loading transactions:', error);
    }
}

// Settings
async function loadSettings() {
    try {
        const response = await fetch('/api/admin/settings');
        const settings = await response.json();
        
        // Game settings
        document.getElementById('setting-card-price').value = settings.cardPrice;
        document.getElementById('setting-prize-percent').value = settings.prizePercent;
        document.getElementById('setting-min-players').value = settings.minPlayers;
        document.getElementById('setting-max-players').value = settings.maxPlayers;
        document.getElementById('setting-call-interval').value = settings.callInterval;
        document.getElementById('setting-selection-time').value = settings.selectionTime;
        
        // Security settings
        document.getElementById('setting-email-verify').checked = settings.emailVerify;
        document.getElementById('setting-max-login').value = settings.maxLoginAttempts;
        document.getElementById('setting-session-timeout').value = settings.sessionTimeout;
        document.getElementById('setting-rate-limit').value = settings.rateLimit;
        
        // Notification settings
        document.getElementById('setting-notify-wins').checked = settings.notifyWins;
        document.getElementById('setting-notify-deposits').checked = settings.notifyDeposits;
        document.getElementById('setting-admin-email').value = settings.adminEmail;
        
        // Load rooms
        loadRoomsSettings(settings.rooms);
        
    } catch (error) {
        console.error('Error loading settings:', error);
    }
}

function loadRoomsSettings(rooms) {
    const container = document.getElementById('rooms-settings-list');
    container.innerHTML = '';
    
    rooms.forEach(room => {
        const roomDiv = document.createElement('div');
        roomDiv.className = 'room-setting-item';
        roomDiv.innerHTML = `
            <div class="room-header">
                <input type="text" value="${room.name}" placeholder="Room Name">
                <button class="btn-icon" onclick="removeRoom('${room.id}')">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
            <div class="room-details">
                <input type="number" value="${room.cardPrice}" placeholder="Price">
                <input type="number" value="${room.minPlayers}" placeholder="Min Players">
                <input type="number" value="${room.maxPlayers}" placeholder="Max Players">
                <select>
                    <option value="classic" ${room.mode === 'classic' ? 'selected' : ''}>Classic</option>
                    <option value="blackout" ${room.mode === 'blackout' ? 'selected' : ''}>Blackout</option>
                    <option value="line" ${room.mode === 'line' ? 'selected' : ''}>Line</option>
                    <option value="corners" ${room.mode === 'corners' ? 'selected' : ''}>Four Corners</option>
                </select>
            </div>
        `;
        container.appendChild(roomDiv);
    });
}

// Analytics
async function loadAnalytics() {
    try {
        const period = document.getElementById('analytics-period').value;
        let start = '', end = '';
        
        if (period === 'custom') {
            start = document.getElementById('analytics-start').value;
            end = document.getElementById('analytics-end').value;
            document.getElementById('custom-date-range').style.display = 'flex';
        } else {
            document.getElementById('custom-date-range').style.display = 'none';
        }
        
        const response = await fetch(`/api/admin/analytics?period=${period}&start=${start}&end=${end}`);
        const data = await response.json();
        
        // Update KPIs
        document.getElementById('arpdau').textContent = formatCurrency(data.arpdau);
        document.getElementById('conversion-rate').textContent = `${data.conversionRate}%`;
        document.getElementById('retention-d1').textContent = `${data.retention.d1}%`;
        document.getElementById('avg-game-duration').textContent = `${data.avgGameDuration}s`;
        
        // Create retention chart
        createRetentionChart(data.retention);
        
        // Create game distribution chart
        createGameDistributionChart(data.gameDistribution);
        
    } catch (error) {
        console.error('Error loading analytics:', error);
    }
}

function createRetentionChart(data) {
    const ctx = document.getElementById('user-retention-chart').getContext('2d');
    
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['D1', 'D3', 'D7', 'D14', 'D30'],
            datasets: [{
                label: 'User Retention',
                data: [data.d1, data.d3, data.d7, data.d14, data.d30],
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'User Retention Curve'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: {
                        callback: value => value + '%'
                    }
                }
            }
        }
    });
}

function createGameDistributionChart(data) {
    const ctx = document.getElementById('game-distribution-chart').getContext('2d');
    
    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Classic', 'Blackout', 'Line', 'Four Corners'],
            datasets: [{
                data: [data.classic, data.blackout, data.line, data.corners],
                backgroundColor: ['#6366f1', '#8b5cf6', '#10b981', '#f59e0b']
            }]
        },
        options: {
            responsive: true,
            plugins: {
                title: {
                    display: true,
                    text: 'Game Mode Distribution'
                }
            }
        }
    });
}

// Audit Logs
async function loadAuditLogs() {
    try {
        const search = document.getElementById('log-search')?.value || '';
        const level = document.getElementById('log-level')?.value || 'all';
        const action = document.getElementById('log-action')?.value || 'all';
        
        const response = await fetch(`/api/admin/logs?search=${search}&level=${level}&action=${action}`);
        const logs = await response.json();
        
        const container = document.getElementById('logs-list');
        container.innerHTML = '';
        
        logs.forEach(log => {
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.innerHTML = `
                <span class="log-level ${log.level}">${log.level}</span>
                <span class="log-time">${formatTime(log.timestamp)}</span>
                <span class="log-message">${log.message}</span>
                <span class="log-user">${log.user}</span>
                <span class="log-ip">${log.ip}</span>
            `;
            container.appendChild(entry);
        });
        
    } catch (error) {
        console.error('Error loading logs:', error);
    }
}

// User Actions
async function viewUser(userId) {
    try {
        const response = await fetch(`/api/admin/users/${userId}`);
        const user = await response.json();
        
        currentUser = user;
        
        const content = document.getElementById('user-details-content');
        content.innerHTML = `
            <div class="user-detail">
                <strong>ID:</strong> ${user.id}
            </div>
            <div class="user-detail">
                <strong>Username:</strong> @${user.username || 'N/A'}
            </div>
            <div class="user-detail">
                <strong>Balance:</strong> ${formatCurrency(user.balance)}
            </div>
            <div class="user-detail">
                <strong>Games Played:</strong> ${user.games_played}
            </div>
            <div class="user-detail">
                <strong>Games Won:</strong> ${user.games_won}
            </div>
            <div class="user-detail">
                <strong>Total Deposits:</strong> ${formatCurrency(user.total_deposits)}
            </div>
            <div class="user-detail">
                <strong>Total Withdrawals:</strong> ${formatCurrency(user.total_withdrawals)}
            </div>
            <div class="user-detail">
                <strong>Joined:</strong> ${formatTime(user.created_at)}
            </div>
            <div class="user-detail">
                <strong>Last Seen:</strong> ${formatTimeAgo(user.last_seen)}
            </div>
        `;
        
        openModal('user-modal');
        
    } catch (error) {
        console.error('Error loading user:', error);
    }
}

function adjustUserBalance(userId) {
    document.getElementById('balance-user-id').value = userId;
    document.getElementById('current-balance').textContent = 
        formatCurrency(currentUser?.balance || 0);
    openModal('balance-modal');
}

async function processBalanceAdjustment() {
    const userId = document.getElementById('balance-user-id').value;
    const amount = parseFloat(document.getElementById('balance-amount').value);
    const type = document.getElementById('balance-type').value;
    const reason = document.getElementById('balance-reason').value;
    
    if (!amount || amount <= 0) {
        alert('Please enter a valid amount');
        return;
    }
    
    try {
        const response = await fetch('/api/admin/adjust-balance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userId, amount, type, reason })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Balance adjusted successfully', 'success');
            closeModal('balance-modal');
            loadUsers(); // Refresh user list
        } else {
            alert(result.message);
        }
        
    } catch (error) {
        console.error('Error adjusting balance:', error);
        alert('Failed to adjust balance');
    }
}

async function toggleUserBan(userId) {
    if (!confirm('Are you sure you want to ban/unban this user?')) return;
    
    try {
        const response = await fetch('/api/admin/toggle-ban', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ userId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('User status updated', 'success');
            loadUsers();
        }
        
    } catch (error) {
        console.error('Error toggling ban:', error);
    }
}

// Game Actions
async function viewGame(gameId) {
    window.open(`/webapp/bingo_game.html?game=${gameId}&admin=true`, '_blank');
}

async function endGame(gameId) {
    if (!confirm('Are you sure you want to end this game?')) return;
    
    try {
        const response = await fetch('/api/admin/end-game', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gameId })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Game ended successfully', 'success');
            loadGames();
        }
        
    } catch (error) {
        console.error('Error ending game:', error);
    }
}

// Broadcast
function broadcastMessage() {
    // Load rooms for selector
    fetch('/api/admin/rooms')
        .then(res => res.json())
        .then(rooms => {
            const select = document.getElementById('broadcast-room');
            select.innerHTML = rooms.map(r => 
                `<option value="${r.id}">${r.name}</option>`
            ).join('');
        });
    
    openModal('broadcast-modal');
}

async function sendBroadcast() {
    const type = document.getElementById('broadcast-type').value;
    const room = document.getElementById('broadcast-room').value;
    const message = document.getElementById('broadcast-message').value;
    const link = document.getElementById('broadcast-link').value;
    
    if (!message) {
        alert('Please enter a message');
        return;
    }
    
    try {
        const response = await fetch('/api/admin/broadcast', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type, room, message, link })
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification(`Broadcast sent to ${result.recipients} users`, 'success');
            closeModal('broadcast-modal');
        }
        
    } catch (error) {
        console.error('Error sending broadcast:', error);
    }
}

// Settings
async function saveSettings() {
    const settings = {
        cardPrice: parseFloat(document.getElementById('setting-card-price').value),
        prizePercent: parseFloat(document.getElementById('setting-prize-percent').value),
        minPlayers: parseInt(document.getElementById('setting-min-players').value),
        maxPlayers: parseInt(document.getElementById('setting-max-players').value),
        callInterval: parseFloat(document.getElementById('setting-call-interval').value),
        selectionTime: parseInt(document.getElementById('setting-selection-time').value),
        emailVerify: document.getElementById('setting-email-verify').checked,
        maxLoginAttempts: parseInt(document.getElementById('setting-max-login').value),
        sessionTimeout: parseInt(document.getElementById('setting-session-timeout').value),
        rateLimit: parseInt(document.getElementById('setting-rate-limit').value),
        notifyWins: document.getElementById('setting-notify-wins').checked,
        notifyDeposits: document.getElementById('setting-notify-deposits').checked,
        adminEmail: document.getElementById('setting-admin-email').value
    };
    
    try {
        const response = await fetch('/api/admin/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showNotification('Settings saved successfully', 'success');
        }
        
    } catch (error) {
        console.error('Error saving settings:', error);
    }
}

// Utility Functions
function loadPageData(page) {
    switch(page) {
        case 'dashboard':
            loadDashboardData();
            break;
        case 'games':
            loadGames();
            break;
        case 'users':
            loadUsers();
            break;
        case 'transactions':
            loadTransactions();
            break;
        case 'settings':
            loadSettings();
            break;
        case 'analytics':
            loadAnalytics();
            break;
        case 'logs':
            loadAuditLogs();
            break;
    }
}

function refreshData() {
    if (currentPage) {
        loadPageData(currentPage);
    }
}

function updateDateTime() {
    const now = new Date();
    document.getElementById('current-datetime').textContent = 
        now.toLocaleString();
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-ET', {
        style: 'currency',
        currency: 'ETB',
        minimumFractionDigits: 0
    }).format(amount);
}

function formatTime(timestamp) {
    return new Date(timestamp).toLocaleString();
}

function formatTimeAgo(timestamp) {
    const now = new Date();
    const then = new Date(timestamp);
    const seconds = Math.floor((now - then) / 1000);
    
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

function formatDuration(seconds) {
    if (!seconds) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
}

function showNotification(message, type = 'info') {
    // You can implement a toast notification system here
    console.log(`[${type}] ${message}`);
    
    // Simple alert for now
    if (type === 'critical') {
        alert(`⚠️ ${message}`);
    }
}

function updateUserCount() {
    // Update user count in real-time
    fetch('/api/admin/stats')
        .then(res => res.json())
        .then(data => {
            document.getElementById('total-users').textContent = data.totalUsers;
        });
}

// Modal Functions
function openModal(modalId) {
    document.getElementById(modalId).classList.add('show');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('show');
}

// Export Functions
function exportUsers() {
    window.location.href = '/api/admin/export/users';
}

function exportTransactions() {
    const from = document.getElementById('tx-date-from')?.value || '';
    const to = document.getElementById('tx-date-to')?.value || '';
    window.location.href = `/api/admin/export/transactions?from=${from}&to=${to}`;
}

// Event Listeners
document.getElementById('game-search')?.addEventListener('input', debounce(loadGames, 500));
document.getElementById('game-status-filter')?.addEventListener('change', loadGames);
document.getElementById('game-room-filter')?.addEventListener('change', loadGames);

document.getElementById('user-search')?.addEventListener('input', debounce(loadUsers, 500));
document.getElementById('user-status-filter')?.addEventListener('change', loadUsers);
document.getElementById('user-sort')?.addEventListener('change', loadUsers);

document.getElementById('analytics-period')?.addEventListener('change', function() {
    if (this.value === 'custom') {
        document.getElementById('custom-date-range').style.display = 'flex';
    } else {
        document.getElementById('custom-date-range').style.display = 'none';
        loadAnalytics();
    }
});

// Debounce helper
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Logout
function logout() {
    localStorage.removeItem('adminToken');
    window.location.href = '/admin/login.html';
}