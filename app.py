import json
import re
import os
import base64
import requests
from io import BytesIO
from PIL import Image
from flask import Flask, request, session, redirect, url_for, jsonify, render_template_string, Response
from datetime import datetime, timedelta
from collections import deque, defaultdict, OrderedDict
from functools import wraps
import time
import hashlib
import html
from urllib.parse import urlparse
import random

app = Flask(__name__)
app.secret_key = "change-this-secret-key-in-production"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# ====================
# ENHANCED DATA STRUCTURES
# ====================

MESSAGES = defaultdict(lambda: deque(maxlen=500))  # room_id -> messages
ROOMS = OrderedDict([("general", {"name": "General Chat", "created_by": "system", "theme": "dark", "privacy": "public"})])
BLACKLIST = {}  # ip -> {"action": "black"|"color", "value": "#000000", "timestamp": datetime}
BANNED_IPS = set()  # IPs that get instant tab close
BANNED_USERS = set()  # Usernames that are banned
USER_EFFECTS = {}  # username -> {"action": "black"|"color", "value": "#000000", "timestamp": datetime}
USER_PROFILES = {}  # username -> {"avatar": "url", "theme": "dark", "layout": "compact"}
ACTIVE_USERS = {}  # username -> {"last_seen": datetime, "ip": "x.x.x.x", "room": "general", "geo": {}}

# GIF and media storage
UPLOADED_GIFS = {}  # gif_id -> {"url": "...", "uploader": "username", "timestamp": datetime}
MESSAGE_METADATA = {}  # message_id -> {"gif_url": "...", "deleted": False}

# Admin credentials
ADMIN_USER = "adminof67"
ADMIN_PASS = "adminof67"

# Available themes
THEMES = {
    "dark": {"bg": "#1a1a2e", "primary": "#16213e", "secondary": "#0f3460", "accent": "#00d4ff"},
    "matrix": {"bg": "#000000", "primary": "#003300", "secondary": "#006600", "accent": "#00ff00"},
    "cyberpunk": {"bg": "#0c0c1c", "primary": "#1a1a3a", "secondary": "#2a2a5a", "accent": "#ff00ff"},
    "ocean": {"bg": "#0a1929", "primary": "#0d2b50", "secondary": "#134074", "accent": "#00e5ff"},
    "sunset": {"bg": "#2c003e", "primary": "#4a0072", "secondary": "#6a00a0", "accent": "#ff8c00"},
    "forest": {"bg": "#0d1b1e", "primary": "#1c3738", "secondary": "#2d4d43", "accent": "#8db600"},
    "midnight": {"bg": "#000814", "primary": "#001d3d", "secondary": "#003566", "accent": "#ffc300"},
    "synthwave": {"bg": "#1a0b2e", "primary": "#2d1b47", "secondary": "#4c2a5c", "accent": "#ff00aa"}
}

# Available chat layouts
LAYOUTS = {
    "compact": {"message_spacing": "8px", "font_size": "14px", "padding": "10px"},
    "modern": {"message_spacing": "15px", "font_size": "16px", "padding": "20px"},
    "bubbles": {"message_spacing": "12px", "font_size": "15px", "padding": "15px", "bubbles": True},
    "minimal": {"message_spacing": "5px", "font_size": "13px", "padding": "5px"}
}

# ====================
# HELPER FUNCTIONS
# ====================

def get_client_ip():
    """Get real client IP even behind proxy"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def get_geolocation(ip):
    """Get approximate geolocation from IP"""
    try:
        # Using ip-api.com (free tier)
        if ip.startswith('127.') or ip.startswith('192.168.') or ip.startswith('10.'):
            return {"country": "Local", "city": "Local Network", "isp": "Private"}
        
        response = requests.get(f'http://ip-api.com/json/{ip}', timeout=3)
        data = response.json()
        if data['status'] == 'success':
            return {
                "country": data.get('country', 'Unknown'),
                "city": data.get('city', 'Unknown'),
                "isp": data.get('isp', 'Unknown'),
                "lat": data.get('lat'),
                "lon": data.get('lon')
            }
    except:
        pass
    return {"country": "Unknown", "city": "Unknown", "isp": "Unknown"}

def validate_gif_url(url):
    """Validate and sanitize GIF URL"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ['http', 'https']:
            return False
        if not parsed.netloc:
            return False
        
        # Check if it's a GIF
        response = requests.head(url, timeout=5)
        content_type = response.headers.get('content-type', '')
        if 'image/gif' in content_type or url.lower().endswith('.gif'):
            return True
        return False
    except:
        return False

def compress_gif_data(gif_data):
    """Compress GIF data while maintaining quality"""
    try:
        img = Image.open(BytesIO(gif_data))
        output = BytesIO()
        # Save with optimization
        img.save(output, format='GIF', save_all=True, optimize=True, loop=0)
        return output.getvalue()
    except:
        return gif_data

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return "Unauthorized", 403
        return f(*args, **kwargs)
    return decorated

def generate_message_id():
    """Generate unique message ID"""
    return hashlib.sha256(f"{time.time()}{random.random()}".encode()).hexdigest()[:16]

def sanitize_html(text):
    """Sanitize HTML to prevent XSS"""
    return html.escape(text)

def generate_ascii_art():
    """Generate cool ASCII art for admin"""
    ascii_arts = [
        """
╔══════════════════════════════════════════╗
║  █████╗ ██████╗ ███╗   ███╗██╗███╗   ██╗ ║
║ ██╔══██╗██╔══██╗████╗ ████║██║████╗  ██║ ║
║ ███████║██║  ██║██╔████╔██║██║██╔██╗ ██║ ║
║ ██╔══██║██║  ██║██║╚██╔╝██║██║██║╚██╗██║ ║
║ ██║  ██║██████╔╝██║ ╚═╝ ██║██║██║ ╚████║ ║
║ ╚═╝  ╚═╝╚═════╝ ╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝ ║
╚══════════════════════════════════════════╝
""",
        """
▓█████▄ ▓█████ ▄▄▄      ▒███████▒▓██   ██▓
▒██▀ ██▌▓█   ▀▒████▄    ▒ ▒ ▒ ▄▀░ ▒██  ██▒
░██   █▌▒███  ▒██  ▀█▄  ░ ▒ ▄▀▒░   ▒██ ██░
░▓█▄   ▌▒▓█  ▄░██▄▄▄▄██   ▄▀▒   ░  ░ ▐██▓░
░▒████▓ ░▒████▒▓█   ▓██▒▒███████▒  ░ ██▒▓░
 ▒▒▓  ▒ ░░ ▒░ ░▒▒   ▓▒█░░▒▒ ▓░▒░▒   ██▒▒▒ 
 ░ ▒  ▒  ░ ░  ░ ▒   ▒▒ ░░░▒ ▒ ░ ▒ ▓██ ░▒░ 
 ░ ░  ░    ░    ░   ▒   ░ ░ ░ ░ ░ ▒ ▒ ░░  
   ░       ░  ░     ░  ░  ░ ░     ░ ░     
 ░                     ░          ░ ░     
""",
        """
╔╦╗┌─┐┌┐┌┌─┐┬─┐┬┌─┐  ╔═╗┌─┐┌┬┐┬┌─┐┌┐┌
 ║ ├┤ │││├┤ ├┬┘│├┤   ╠╣ │ │ │ ││ ││││
 ╩ └─┘┘└┘└─┘┴└─┴└─┘  ╚  └─┘ ┴ ┴└─┘┘└┘
"""
    ]
    return random.choice(ascii_arts)

# ====================
# ENHANCED HTML TEMPLATE WITH ALL FEATURES
# ====================

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{% if is_admin %}🛡️ Admin {% endif %}Enhanced Chat</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    
    <!-- Anti-copy/capture meta tags -->
    <meta name="robots" content="noindex, nofollow">
    <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    
    <style>
        :root {
            --bg-color: {{ theme_colors.bg }};
            --primary-color: {{ theme_colors.primary }};
            --secondary-color: {{ theme_colors.secondary }};
            --accent-color: {{ theme_colors.accent }};
            --message-spacing: {{ layout_settings.message_spacing }};
            --font-size: {{ layout_settings.font_size }};
            --padding: {{ layout_settings.padding }};
            {% if layout_settings.get('bubbles') %}
            --message-radius: 20px;
            {% else %}
            --message-radius: 8px;
            {% endif %}
        }
        
        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box; 
            user-select: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
        }
        
        body {
            font-family: 'Segoe UI', 'Roboto', Arial, sans-serif;
            background: var(--bg-color);
            color: #eee;
            min-height: 100vh;
            overflow-x: hidden;
            -webkit-touch-callout: none;
            -webkit-user-drag: none;
        }
        
        /* Print protection overlay */
        .print-protection {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: #000;
            z-index: 9999999;
            justify-content: center;
            align-items: center;
            color: red;
            font-size: 24px;
            font-weight: bold;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: var(--padding);
        }
        
        /* ASCII Art Header for Admin */
        .ascii-art {
            font-family: 'Courier New', monospace;
            color: #00ff00;
            background: #000;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 10px;
            font-size: 12px;
            line-height: 1.2;
            white-space: pre;
        }
        
        .top-bar {
            background: var(--primary-color);
            padding: 15px 25px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 6px 12px rgba(0,0,0,0.3);
            position: relative;
            overflow: hidden;
        }
        
        .top-bar::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--accent-color), #ff00ff, #00ffff);
        }
        
        h1 {
            font-size: 30px;
            color: var(--accent-color);
            text-shadow: 0 0 10px rgba(0,212,255,0.3);
            background: linear-gradient(90deg, var(--accent-color), #ffffff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .user-info {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .badge {
            background: linear-gradient(45deg, #ff0000, #ff6b6b);
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .admin-badge {
            background: linear-gradient(45deg, #00d4ff, #0080ff);
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.7; }
            100% { opacity: 1; }
        }
        
        .geo-badge {
            background: #2ecc71;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 10px;
        }
        
        .theme-selector {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 1000;
            background: var(--primary-color);
            padding: 10px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            display: flex;
            gap: 10px;
        }
        
        .theme-dot {
            width: 25px;
            height: 25px;
            border-radius: 50%;
            cursor: pointer;
            border: 2px solid transparent;
            transition: transform 0.3s, border-color 0.3s;
        }
        
        .theme-dot:hover {
            transform: scale(1.2);
            border-color: white;
        }
        
        .theme-dot.active {
            border-color: white;
            transform: scale(1.1);
        }
        
        .settings-panel {
            position: fixed;
            top: 20px;
            left: 20px;
            z-index: 1000;
            background: var(--primary-color);
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            width: 250px;
            display: none;
        }
        
        .settings-panel.show {
            display: block;
            animation: slideIn 0.3s ease;
        }
        
        @keyframes slideIn {
            from { transform: translateX(-100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        .main-content {
            display: flex;
            gap: 25px;
        }
        
        .sidebar {
            width: 280px;
            background: var(--primary-color);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 6px 12px rgba(0,0,0,0.3);
            height: fit-content;
            position: sticky;
            top: 20px;
        }
        
        .sidebar h3 {
            color: var(--accent-color);
            margin-bottom: 20px;
            font-size: 20px;
            border-bottom: 2px solid var(--accent-color);
            padding-bottom: 8px;
        }
        
        .room-list {
            list-style: none;
        }
        
        .room-item {
            padding: 12px 15px;
            margin-bottom: 10px;
            background: var(--secondary-color);
            border-radius: var(--message-radius);
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .room-item:hover {
            background: #1a4d7a;
            transform: translateX(8px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }
        
        .room-item.active {
            background: var(--accent-color);
            color: #000;
            font-weight: bold;
            box-shadow: 0 0 15px rgba(0,212,255,0.5);
        }
        
        .room-user-count {
            background: rgba(0,0,0,0.3);
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
        }
        
        .chat-area {
            flex: 1;
            background: var(--primary-color);
            border-radius: 12px;
            padding: 25px;
            box-shadow: 0 6px 12px rgba(0,0,0,0.3);
            display: flex;
            flex-direction: column;
            min-height: 700px;
        }
        
        #messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background: var(--secondary-color);
            border-radius: 12px;
            margin-bottom: 20px;
            scroll-behavior: smooth;
        }
        
        {% if layout_settings.get('bubbles') %}
        .msg {
            margin-bottom: var(--message-spacing);
            padding: 12px 18px;
            background: var(--accent-color);
            color: #000;
            border-radius: 20px 20px 20px 5px;
            max-width: 80%;
            position: relative;
            animation: fadeIn 0.3s ease;
        }
        
        .msg.own {
            background: #2ecc71;
            color: white;
            border-radius: 20px 20px 5px 20px;
            margin-left: auto;
        }
        {% else %}
        .msg {
            margin-bottom: var(--message-spacing);
            padding: 12px 15px;
            background: #1a4d7a;
            border-radius: var(--message-radius);
            animation: fadeIn 0.3s ease;
            border-left: 4px solid var(--accent-color);
        }
        {% endif %}
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .msg .time {
            color: #888;
            font-size: 11px;
            margin-right: 10px;
        }
        
        .msg .user {
            font-weight: bold;
            color: var(--accent-color);
            margin-right: 10px;
        }
        
        .msg .text {
            color: #eee;
            word-break: break-word;
            line-height: 1.5;
        }
        
        .msg.deleted {
            opacity: 0.5;
            font-style: italic;
            color: #888;
        }
        
        .msg-actions {
            position: absolute;
            right: 10px;
            top: 10px;
            opacity: 0;
            transition: opacity 0.3s;
        }
        
        .msg:hover .msg-actions {
            opacity: 1;
        }
        
        .delete-btn {
            background: #e74c3c;
            color: white;
            border: none;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
        }
        
        .gif-container {
            margin: 10px 0;
            border-radius: 8px;
            overflow: hidden;
            max-width: 300px;
        }
        
        .gif-container img {
            width: 100%;
            border-radius: 8px;
        }
        
        .chat-input {
            display: flex;
            gap: 15px;
            align-items: center;
        }
        
        input[type="text"], input[type="password"] {
            flex: 1;
            padding: 14px;
            border-radius: 8px;
            border: 2px solid var(--secondary-color);
            background: var(--secondary-color);
            color: #eee;
            font-size: var(--font-size);
            transition: all 0.3s;
        }
        
        input:focus {
            outline: none;
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(0,212,255,0.2);
        }
        
        .gif-input {
            display: flex;
            gap: 10px;
            margin-top: 10px;
            display: none;
        }
        
        .gif-input.show {
            display: flex;
        }
        
        button {
            padding: 12px 24px;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .btn-primary {
            background: linear-gradient(45deg, var(--accent-color), #0080ff);
            color: #000;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,212,255,0.3);
        }
        
        .btn-danger {
            background: linear-gradient(45deg, #e74c3c, #c0392b);
            color: #fff;
        }
        
        .btn-danger:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(231,76,60,0.3);
        }
        
        .btn-success {
            background: linear-gradient(45deg, #2ecc71, #27ae60);
            color: #fff;
        }
        
        .btn-success:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(46,204,113,0.3);
        }
        
        .btn-warning {
            background: linear-gradient(45deg, #f39c12, #d35400);
            color: #fff;
        }
        
        .login-box, .admin-panel {
            background: var(--primary-color);
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.4);
            max-width: 450px;
            margin: 100px auto;
            animation: fadeInUp 0.5s ease;
        }
        
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(30px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .admin-panel {
            max-width: 100%;
            margin: 30px 0;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: var(--accent-color);
            font-weight: bold;
        }
        
        .admin-controls {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 25px;
            margin-top: 25px;
        }
        
        .control-box {
            background: var(--secondary-color);
            padding: 25px;
            border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .control-box h4 {
            color: var(--accent-color);
            margin-bottom: 20px;
            font-size: 18px;
            border-bottom: 2px solid var(--accent-color);
            padding-bottom: 10px;
        }
        
        select {
            width: 100%;
            padding: 12px;
            border-radius: 6px;
            border: 2px solid var(--accent-color);
            background: var(--primary-color);
            color: #eee;
            margin-bottom: 15px;
        }
        
        .user-list {
            max-height: 250px;
            overflow-y: auto;
            background: var(--primary-color);
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
        }
        
        .user-item {
            padding: 10px;
            margin: 5px 0;
            background: #1a4d7a;
            border-radius: 6px;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .user-ip {
            font-size: 11px;
            color: #aaa;
        }
        
        .user-geo {
            font-size: 11px;
            color: #2ecc71;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        
        .stat-box {
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 28px;
            font-weight: bold;
            color: var(--accent-color);
        }
        
        .stat-label {
            font-size: 12px;
            color: #aaa;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .undercover-mode {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 1000;
            background: #ff9800;
            color: #000;
            padding: 10px 15px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 12px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.3);
        }
        
        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            background: #2ecc71;
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 9999;
            animation: slideInRight 0.3s ease;
        }
        
        @keyframes slideInRight {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        
        .message-typing {
            color: #aaa;
            font-size: 12px;
            margin-top: 5px;
            font-style: italic;
        }
        
        /* Screen capture protection */
        .capture-protection {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 9998;
            background: repeating-linear-gradient(
                45deg,
                transparent,
                transparent 10px,
                rgba(255,0,0,0.03) 10px,
                rgba(255,0,0,0.03) 20px
            );
            display: none;
        }
    </style>
</head>
<body>
    <div class="print-protection" id="printProtection">
        <div style="text-align: center;">
            <h1 style="color: red;">⚠️ PRINTING BLOCKED ⚠️</h1>
            <p>This page is protected against printing.</p>
            <p>Please close this dialog and refresh the page.</p>
            <button onclick="closePrintProtection()" style="margin-top: 20px; padding: 10px 20px; background: red; color: white; border: none; border-radius: 5px; cursor: pointer;">
                Close & Refresh
            </button>
        </div>
    </div>
    
    <div class="capture-protection" id="captureProtection"></div>
    
    {% if username and not is_admin %}
    <div class="theme-selector" id="themeSelector">
        {% for theme_id, theme in themes.items() %}
        <div class="theme-dot {% if theme_id == user_theme %}active{% endif %}" 
             style="background: {{ theme.bg }};"
             onclick="switchTheme('{{ theme_id }}')"
             title="{{ theme_id|title }}"></div>
        {% endfor %}
    </div>
    {% endif %}
    
    {% if username and not is_admin %}
    <div class="settings-panel" id="settingsPanel">
        <h3 style="margin-bottom: 15px;">Settings</h3>
        <div class="form-group">
            <label>Chat Layout:</label>
            <select id="layoutSelect" onchange="switchLayout(this.value)">
                {% for layout_id, layout in layouts.items() %}
                <option value="{{ layout_id }}" {% if layout_id == user_layout %}selected{% endif %}>
                    {{ layout_id|title }}
                </option>
                {% endfor %}
            </select>
        </div>
        <button onclick="toggleSettings()" class="btn-primary" style="width: 100%; margin-top: 10px;">
            Close Settings
        </button>
    </div>
    {% endif %}
    
    <div class="container">
        <div class="top-bar">
            <h1>{% if is_admin %}🛡️ {% endif %}Enhanced Chat {% if is_admin %} - ADMIN MODE{% endif %}</h1>
            {% if username %}
            <div class="user-info">
                {% if is_admin %}
                    <div class="ascii-art">
{{ admin_ascii_art }}
                    </div>
                    <span class="badge admin-badge">SUPER ADMIN</span>
                    {% if admin_undercover %}
                    <span class="badge" style="background: #ff9800;">UNDERCOVER</span>
                    {% endif %}
                {% endif %}
                {% if geo_data %}
                <span class="geo-badge" title="{{ geo_data.city }}, {{ geo_data.country }}">
                    🌍 {{ geo_data.country|upper }}
                </span>
                {% endif %}
                <span>{{ username }}</span>
                {% if is_admin %}
                <button onclick="toggleUndercover()" class="btn-warning">
                    {{ 'Exit Undercover' if admin_undercover else 'Go Undercover' }}
                </button>
                {% endif %}
                <button onclick="toggleSettings()" class="btn-primary">
                    ⚙️ Settings
                </button>
                <form method="POST" action="{{ url_for('logout') }}" style="display:inline;">
                    <button type="submit" class="btn-danger">Logout</button>
                </form>
            </div>
            {% endif %}
        </div>

        {% if not username %}
        <div class="login-box">
            <h2 style="color: var(--accent-color); margin-bottom: 25px; text-align: center;">Join Enhanced Chat</h2>
            <form method="POST" action="{{ url_for('set_username') }}">
                <div class="form-group">
                    <label>Username:</label>
                    <input type="text" name="username" required maxlength="20" autocomplete="off" placeholder="Enter your username">
                </div>
                <div class="form-group">
                    <label>Avatar URL (optional):</label>
                    <input type="text" name="avatar" placeholder="https://example.com/avatar.png">
                </div>
                <button type="submit" class="btn-primary" style="width: 100%; padding: 15px; font-size: 16px;">
                    🚀 Enter Chat
                </button>
            </form>
        </div>
        {% else %}
        
        {% if is_admin %}
        <div class="admin-panel">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 25px;">
                <h3 style="color: var(--accent-color);">🛡️ Admin Control Panel</h3>
                <div style="display: flex; gap: 10px;">
                    <button onclick="refreshAll()" class="btn-primary">🔄 Refresh All</button>
                    <button onclick="exportData()" class="btn-success">📊 Export Data</button>
                </div>
            </div>
            
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="stat-value">{{ stats.active_users }}</div>
                    <div class="stat-label">Active Users</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{{ stats.total_messages }}</div>
                    <div class="stat-label">Total Messages</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{{ stats.banned_count }}</div>
                    <div class="stat-label">Banned Users</div>
                </div>
                <div class="stat-box">
                    <div class="stat-value">{{ stats.rooms_count }}</div>
                    <div class="stat-label">Rooms</div>
                </div>
            </div>
            
            <div class="admin-controls">
                <div class="control-box">
                    <h4>Room Management</h4>
                    <input type="text" id="new-room-name" placeholder="Room name" maxlength="30" style="margin-bottom: 10px;">
                    <select id="room-privacy">
                        <option value="public">Public</option>
                        <option value="private">Private</option>
                        <option value="hidden">Hidden</option>
                    </select>
                    <button onclick="createRoom()" class="btn-success" style="width: 100%; margin-top: 10px;">Create Room</button>
                    
                    <div style="margin-top: 15px;">
                        <h5 style="color: #aaa; margin-bottom: 10px;">Existing Rooms</h5>
                        <div id="rooms-list" class="user-list"></div>
                    </div>
                </div>
                
                <div class="control-box">
                    <h4>User Control</h4>
                    <select id="screen-target-type">
                        <option value="ip">By IP</option>
                        <option value="user">By Username</option>
                    </select>
                    <select id="screen-action">
                        <option value="black">Black Screen</option>
                        <option value="color">Custom Color</option>
                        <option value="blink">Blinking Screen</option>
                        <option value="invert">Invert Colors</option>
                    </select>
                    <input type="color" id="screen-color" value="#000000" style="margin-bottom: 10px;">
                    <input type="text" id="target-identifier" placeholder="Target IP or Username" style="margin-bottom: 10px;">
                    <button onclick="applyScreenEffect()" class="btn-primary" style="width: 100%; margin-bottom: 5px;">Apply Effect</button>
                    <button onclick="clearScreenEffect()" class="btn-warning" style="width: 100%;">Clear Effect</button>
                </div>
                
                <div class="control-box">
                    <h4>Ban Management</h4>
                    <select id="ban-type">
                        <option value="ip">Ban IP (Tab Close)</option>
                        <option value="user">Ban Username</option>
                    </select>
                    <input type="text" id="ban-identifier" placeholder="IP or Username">
                    <textarea id="ban-reason" placeholder="Reason for ban" rows="2" style="width: 100%; margin: 10px 0; padding: 8px; border-radius: 4px; background: var(--primary-color); color: #eee; border: 1px solid #444;"></textarea>
                    <button onclick="banUser()" class="btn-danger" style="width: 100%; margin-top: 5px;">Ban User</button>
                    <button onclick="unbanUser()" class="btn-success" style="width: 100%; margin-top: 5px;">Unban User</button>
                    <button onclick="massUnban()" class="btn-warning" style="width: 100%; margin-top: 5px;">Mass Unban All</button>
                </div>
                
                <div class="control-box">
                    <h4>Active Users & Monitoring</h4>
                    <div id="active-users" class="user-list"></div>
                    <div style="margin-top: 10px; display: flex; gap: 10px;">
                        <button onclick="refreshUsers()" class="btn-primary" style="flex: 1;">Refresh Users</button>
                        <button onclick="sendGlobalMessage()" class="btn-success" style="flex: 1;">Global Message</button>
                    </div>
                    
                    <div style="margin-top: 15px; padding: 15px; background: var(--primary-color); border-radius: 8px; font-size: 12px;">
                        <strong style="color: var(--accent-color);">System Status:</strong>
                        <div id="debug-info" style="margin-top: 8px; line-height: 1.6;"></div>
                    </div>
                </div>
                
                <div class="control-box">
                    <h4>Message Management</h4>
                    <select id="message-action">
                        <option value="delete">Delete User Messages</option>
                        <option value="clear">Clear Room Messages</option>
                        <option value="export">Export Messages</option>
                    </select>
                    <input type="text" id="message-target" placeholder="Username or Room ID">
                    <button onclick="manageMessages()" class="btn-primary" style="width: 100%; margin-top: 10px;">Execute</button>
                    
                    <div style="margin-top: 15px;">
                        <h5 style="color: #aaa; margin-bottom: 10px;">Recent Messages</h5>
                        <div id="recent-messages" class="user-list" style="max-height: 150px;"></div>
                    </div>
                </div>
                
                <div class="control-box">
                    <h4>Security Tools</h4>
                    <button onclick="enableCaptureProtection()" class="btn-primary" style="width: 100%; margin-bottom: 5px;">
                        🔒 Enable Screen Protection
                    </button>
                    <button onclick="disableCaptureProtection()" class="btn-warning" style="width: 100%; margin-bottom: 5px;">
                        🔓 Disable Screen Protection
                    </button>
                    <button onclick="forceReconnectAll()" class="btn-danger" style="width: 100%;">
                        🔄 Force All Users Reconnect
                    </button>
                    <div style="margin-top: 15px; padding: 10px; background: rgba(255,0,0,0.1); border-radius: 4px;">
                        <small style="color: #ff6b6b;">⚠️ These actions affect all users immediately</small>
                    </div>
                </div>
            </div>
        </div>
        {% endif %}
        
        <div class="main-content">
            <div class="sidebar">
                <h3>Rooms</h3>
                <ul class="room-list" id="room-list"></ul>
                <div style="margin-top: 20px; padding-top: 15px; border-top: 1px solid #444;">
                    <h4 style="color: #aaa; font-size: 14px; margin-bottom: 10px;">Online Users</h4>
                    <div id="online-users" class="user-list" style="max-height: 200px;"></div>
                </div>
            </div>
            
            <div class="chat-area">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <h3 style="color: var(--accent-color);" id="room-title">Select a room</h3>
                    <div id="room-info" style="color: #aaa; font-size: 12px;"></div>
                </div>
                
                <div id="messages"></div>
                <div id="typing-indicator" class="message-typing"></div>
                
                <div class="chat-input">
                    <input type="text" id="message-text" placeholder="Type a message or paste GIF URL..." autocomplete="off" required maxlength="500" onkeyup="checkForGif(this.value)" onkeypress="handleTyping(event)">
                    <button type="button" onclick="toggleGifInput()" class="btn-primary" style="padding: 12px 15px;">GIF</button>
                    <button type="submit" onclick="sendMessage(event)" class="btn-primary">Send</button>
                </div>
                
                <div class="gif-input" id="gifInput">
                    <input type="text" id="gif-url" placeholder="Paste GIF URL here..." style="flex: 1;">
                    <button onclick="sendGif()" class="btn-success">Send GIF</button>
                    <button onclick="toggleGifInput()" class="btn-danger">Cancel</button>
                </div>
            </div>
        </div>
        {% endif %}
    </div>
</body>
</html>

<script>
    const username = "{{ username or '' }}";
    const isAdmin = {{ 'true' if is_admin else 'false' }};
    const adminUndercover = {{ 'true' if session.get('admin_undercover') else 'false' }};
    let currentRoom = "general";
    let lastIndex = {};
    let typingTimeout = null;
    let captureProtection = false;
    let userTheme = "{{ user_theme or 'dark' }}";
    let userLayout = "{{ user_layout or 'modern' }}";
    let activeUsers = [];

    // Print protection
    window.addEventListener('beforeprint', (event) => {
        event.preventDefault();
        document.getElementById('printProtection').style.display = 'flex';
        return false;
    });

    // Ctrl+P and Print Screen protection
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey && e.key === 'p') || e.key === 'PrintScreen') {
            e.preventDefault();
            document.getElementById('printProtection').style.display = 'flex';
            // Blink screen
            document.body.style.backgroundColor = '#000';
            setTimeout(() => {
                document.body.style.backgroundColor = '';
            }, 100);
            return false;
        }
    });

    // Screen capture protection
    function enableCaptureProtection() {
        captureProtection = true;
        document.getElementById('captureProtection').style.display = 'block';
        showNotification('Screen capture protection enabled');
    }

    function disableCaptureProtection() {
        captureProtection = false;
        document.getElementById('captureProtection').style.display = 'none';
        showNotification('Screen capture protection disabled');
    }

    function closePrintProtection() {
        document.getElementById('printProtection').style.display = 'none';
        location.reload();
    }

    function showNotification(message, type = 'success') {
        const notification = document.createElement('div');
        notification.className = 'notification';
        notification.textContent = message;
        notification.style.background = type === 'error' ? '#e74c3c' : type === 'warning' ? '#f39c12' : '#2ecc71';
        document.body.appendChild(notification);
        setTimeout(() => {
            notification.remove();
        }, 3000);
    }

    function toggleSettings() {
        const panel = document.getElementById('settingsPanel');
        panel.classList.toggle('show');
    }

    function switchTheme(themeId) {
        fetch("/switch-theme", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({theme: themeId})
        })
        .then(res => {
            if (res.ok) {
                userTheme = themeId;
                document.querySelectorAll('.theme-dot').forEach(dot => {
                    dot.classList.remove('active');
                });
                event.target.classList.add('active');
                location.reload();
            }
        });
    }

    function switchLayout(layoutId) {
        fetch("/switch-layout", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({layout: layoutId})
        })
        .then(res => {
            if (res.ok) {
                userLayout = layoutId;
                location.reload();
            }
        });
    }

    function toggleGifInput() {
        const gifInput = document.getElementById('gifInput');
        gifInput.classList.toggle('show');
    }

    function checkForGif(text) {
        if (text.includes('.gif') && text.startsWith('http')) {
            const gifInput = document.getElementById('gifInput');
            if (!gifInput.classList.contains('show')) {
                gifInput.classList.add('show');
                document.getElementById('gif-url').value = text;
            }
        }
    }

    function handleTyping(e) {
        if (typingTimeout) clearTimeout(typingTimeout);
        
        fetch("/typing", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                room: currentRoom,
                username: username,
                typing: true
            })
        });
        
        typingTimeout = setTimeout(() => {
            fetch("/typing", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    room: currentRoom,
                    username: username,
                    typing: false
                })
            });
        }, 2000);
    }

    function sendGif() {
        const gifUrl = document.getElementById('gif-url').value.trim();
        if (!gifUrl || !gifUrl.includes('.gif')) {
            showNotification('Please enter a valid GIF URL', 'error');
            return;
        }

        fetch("/send-gif", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                url: gifUrl,
                room: currentRoom
            })
        })
        .then(res => {
            if (res.ok) {
                document.getElementById('gif-url').value = '';
                document.getElementById('gifInput').classList.remove('show');
                showNotification('GIF sent successfully!');
            } else {
                showNotification('Failed to send GIF', 'error');
            }
        });
    }

    function deleteMessage(messageId) {
        if (!confirm('Are you sure you want to delete this message?')) return;
        
        fetch("/delete-message", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                message_id: messageId,
                room: currentRoom
            })
        })
        .then(res => {
            if (res.ok) {
                showNotification('Message deleted');
                fetchMessages();
            }
        });
    }

    function toggleUndercover() {
        fetch("/admin/toggle-undercover", {
            method: "POST"
        })
        .then(res => {
            if (res.ok) {
                location.reload();
            }
        });
    }

    {% if username %}
    window.onload = () => {
        loadRooms();
        checkForEffects();
        loadOnlineUsers();
        fetchMessages();
        
        setInterval(() => {
            if (currentRoom) {
                fetchMessages();
                loadOnlineUsers();
                checkForEffects();
                checkTyping();
            }
        }, 1000);
        
        setInterval(() => {
            updateActiveStatus();
        }, 30000);
    };

    function updateActiveStatus() {
        fetch("/update-active", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                room: currentRoom,
                username: username
            })
        });
    }

    function checkForEffects() {
        fetch("/check-effects", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username: username})
        })
            .then(res => res.json())
            .then(data => {
                if (data.banned) {
                    localStorage.setItem('banned_user', username);
                    document.body.innerHTML = `
                        <div style="
                            position: fixed;
                            top: 0;
                            left: 0;
                            width: 100%;
                            height: 100%;
                            background: #000;
                            color: red;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            font-size: 28px;
                            text-align: center;
                            flex-direction: column;
                            z-index: 999999;
                        ">
                            <div>🚫 YOU HAVE BEEN BANNED 🚫</div>
                            <div style="margin-top: 20px; font-size: 16px; color: #aaa;">
                                Reason: ${data.reason || 'Violation of terms'}
                            </div>
                            <div style="margin-top: 10px; font-size: 14px; color: #666;">
                                IP: ${data.ip || 'Logged'}
                            </div>
                        </div>
                    `;
                    setTimeout(() => {
                        window.close();
                        window.location.href = "about:blank";
                    }, 3000);
                } else if (data.effect) {
                    applyEffect(data.effect, data.color, data.duration);
                }
            })
            .catch(err => console.error("Error checking effects:", err));
    }

    function applyEffect(effect, color, duration = 0) {
        switch(effect) {
            case 'black':
                document.body.style.backgroundColor = "#000";
                document.body.style.color = "#fff";
                document.querySelector('.container').style.display = 'none';
                break;
            case 'color':
                document.body.style.backgroundColor = color;
                document.querySelector('.container').style.display = 'none';
                break;
            case 'blink':
                setInterval(() => {
                    document.body.style.backgroundColor = 
                        document.body.style.backgroundColor === '#000' ? color : '#000';
                }, 500);
                break;
            case 'invert':
                document.body.style.filter = 'invert(1)';
                break;
        }
        
        if (duration > 0) {
            setTimeout(() => {
                location.reload();
            }, duration * 1000);
        }
    }

    function loadRooms() {
        fetch("/rooms")
            .then(res => res.json())
            .then(data => {
                const list = document.getElementById("room-list");
                list.innerHTML = "";
                data.rooms.forEach(room => {
                    const li = document.createElement("li");
                    li.className = "room-item" + (room.id === currentRoom ? " active" : "");
                    li.innerHTML = `
                        <span>${escapeHtml(room.name)}</span>
                        <span class="room-user-count">${room.user_count || 0}</span>
                    `;
                    li.onclick = () => switchRoom(room.id, room.name);
                    list.appendChild(li);
                });
            });
    }

    function loadOnlineUsers() {
        fetch("/online-users?room=" + currentRoom)
            .then(res => res.json())
            .then(data => {
                const container = document.getElementById("online-users");
                container.innerHTML = "";
                data.users.forEach(user => {
                    const div = document.createElement("div");
                    div.className = "user-item";
                    div.innerHTML = `
                        <span>${escapeHtml(user.username)}</span>
                        <span class="user-geo">${user.geo?.country || ''}</span>
                    `;
                    container.appendChild(div);
                });
                activeUsers = data.users;
            });
    }

    function checkTyping() {
        fetch("/typing-status?room=" + currentRoom)
            .then(res => res.json())
            .then(data => {
                const indicator = document.getElementById("typing-indicator");
                if (data.typing.length > 0) {
                    indicator.textContent = `${data.typing.join(', ')} ${data.typing.length === 1 ? 'is' : 'are'} typing...`;
                } else {
                    indicator.textContent = '';
                }
            });
    }

    function switchRoom(roomId, roomName) {
        currentRoom = roomId;
        document.getElementById("room-title").textContent = roomName;
        document.getElementById("messages").innerHTML = "";
        lastIndex[currentRoom] = 0;
        loadRooms();
        loadOnlineUsers();
        fetchMessages();
    }

    function fetchMessages() {
        if (!currentRoom) return;
        const after = lastIndex[currentRoom] || 0;
        fetch(`/messages?room=${currentRoom}&after=${after}`)
            .then(res => res.json())
            .then(data => {
                const messagesDiv = document.getElementById("messages");
                data.messages.forEach(msg => {
                    if (msg.deleted) return;
                    
                    const div = document.createElement("div");
                    div.className = "msg" + (msg.user === username ? " own" : "");
                    
                    let content = escapeHtml(msg.text);
                    if (msg.gif_url) {
                        content = `<div class="gif-container"><img src="${escapeHtml(msg.gif_url)}" alt="GIF" loading="lazy"></div>`;
                    }
                    
                    div.innerHTML = `
                        <div style="position: relative;">
                            <span class='time'>[${msg.time}]</span>
                            <span class='user'>${escapeHtml(msg.user)}:</span>
                            <span class='text'>${content}</span>
                            ${msg.user === username ? `
                                <div class="msg-actions">
                                    <button class="delete-btn" onclick="deleteMessage('${msg.id}')">Delete</button>
                                </div>
                            ` : ''}
                        </div>
                    `;
                    messagesDiv.appendChild(div);
                    messagesDiv.scrollTop = messagesDiv.scrollHeight;
                });
                lastIndex[currentRoom] = data.last_index;
            })
            .catch(err => console.error(err));
    }

    function sendMessage(e) {
        if (e) e.preventDefault();
        const input = document.getElementById("message-text");
        const text = input.value.trim();
        if (!text || !currentRoom) return;

        fetch("/send", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({text: text, room: currentRoom})
        }).then(res => {
            if (res.ok) input.value = "";
        });
    }

    function escapeHtml(str) {
        if (!str) return '';
        return str.toString()
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    {% if is_admin %}
    function createRoom() {
        const name = document.getElementById("new-room-name").value.trim();
        const privacy = document.getElementById("room-privacy").value;
        if (!name) return;
        
        fetch("/admin/create-room", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name: name, privacy: privacy})
        }).then(res => {
            if (res.ok) {
                document.getElementById("new-room-name").value = "";
                loadRooms();
                showNotification('Room created successfully');
            }
        });
    }

    function applyScreenEffect() {
        const targetType = document.getElementById("screen-target-type").value;
        const action = document.getElementById("screen-action").value;
        const color = document.getElementById("screen-color").value;
        const identifier = document.getElementById("target-identifier").value.trim();
        if (!identifier) return showNotification("Enter target identifier", "error");
        
        fetch("/admin/screen-effect", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                type: targetType,
                identifier: identifier,
                action: action,
                color: color,
                duration: document.getElementById("effect-duration")?.value || 0
            })
        }).then(res => {
            if (res.ok) {
                showNotification("Effect applied to " + identifier);
            } else {
                res.text().then(msg => showNotification("Failed: " + msg, "error"));
            }
        }).catch(err => showNotification("Error: " + err, "error"));
    }

    function clearScreenEffect() {
        const targetType = document.getElementById("screen-target-type").value;
        const identifier = document.getElementById("target-identifier").value.trim();
        if (!identifier) return showNotification("Enter target identifier", "error");
        
        fetch("/admin/clear-effect", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                type: targetType,
                identifier: identifier
            })
        }).then(res => {
            if (res.ok) {
                showNotification("Effect cleared for " + identifier);
            }
        });
    }

    function banUser() {
        const banType = document.getElementById("ban-type").value;
        const identifier = document.getElementById("ban-identifier").value.trim();
        const reason = document.getElementById("ban-reason").value.trim();
        if (!identifier) return showNotification("Enter identifier", "error");
        
        fetch("/admin/ban", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                type: banType,
                identifier: identifier,
                reason: reason,
                ban: true
            })
        }).then(res => {
            if (res.ok) {
                showNotification(`${banType === 'ip' ? 'IP' : 'User'} ${identifier} banned`);
            } else {
                res.text().then(msg => showNotification("Failed: " + msg, "error"));
            }
        }).catch(err => showNotification("Error: " + err, "error"));
    }

    function unbanUser() {
        const banType = document.getElementById("ban-type").value;
        const identifier = document.getElementById("ban-identifier").value.trim();
        if (!identifier) return showNotification("Enter identifier", "error");
        
        fetch("/admin/ban", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                type: banType,
                identifier: identifier,
                ban: false
            })
        }).then(res => {
            if (res.ok) {
                showNotification(`${banType === 'ip' ? 'IP' : 'User'} ${identifier} unbanned`);
            } else {
                res.text().then(msg => showNotification("Failed: " + msg, "error"));
            }
        }).catch(err => showNotification("Error: " + err, "error"));
    }

    function massUnban() {
        if (!confirm('Are you sure you want to unban ALL users and IPs?')) return;
        
        fetch("/admin/mass-unban", {
            method: "POST"
        }).then(res => {
            if (res.ok) {
                showNotification('All bans cleared');
            }
        });
    }

    function refreshUsers() {
        fetch("/admin/active-users")
            .then(res => res.json())
            .then(data => {
                const div = document.getElementById("active-users");
                div.innerHTML = data.users.map(u => `
                    <div class="user-item">
                        <div>
                            <strong>${u.username}</strong>
                            <div class="user-ip">IP: ${u.ip}</div>
                            <div class="user-geo">${u.geo?.city || ''}, ${u.geo?.country || ''}</div>
                        </div>
                        <button onclick="adminMessageUser('${u.username}')" style="padding: 3px 8px; font-size: 11px;">Message</button>
                    </div>
                `).join("");
            });
        
        fetch("/admin/debug-info")
            .then(res => res.json())
            .then(data => {
                const debugDiv = document.getElementById("debug-info");
                debugDiv.innerHTML = `
                    <strong>System:</strong> ${data.system_stats.total_users} users, ${data.system_stats.total_messages} messages<br>
                    <strong>Banned Users:</strong> ${data.banned_users.join(', ') || 'None'}<br>
                    <strong>Banned IPs:</strong> ${data.banned_ips.slice(0, 5).join(', ') || 'None'}<br>
                    <strong>Active Effects:</strong> ${data.active_effects.join(', ') || 'None'}
                `;
            });
    }

    function adminMessageUser(targetUser) {
        const message = prompt(`Send message to ${targetUser}:`);
        if (message) {
            fetch("/admin/message-user", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    username: targetUser,
                    message: message
                })
            }).then(res => {
                if (res.ok) showNotification(`Message sent to ${targetUser}`);
            });
        }
    }

    function sendGlobalMessage() {
        const message = prompt("Global message to all users:");
        if (message) {
            fetch("/admin/global-message", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({message: message})
            }).then(res => {
                if (res.ok) showNotification('Global message sent');
            });
        }
    }

    function manageMessages() {
        const action = document.getElementById("message-action").value;
        const target = document.getElementById("message-target").value.trim();
        
        if (!target && action !== 'export') return showNotification("Enter target", "error");
        
        fetch("/admin/manage-messages", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                action: action,
                target: target,
                room: currentRoom
            })
        }).then(res => {
            if (res.ok) {
                showNotification(`Action "${action}" completed`);
                if (action === 'delete' || action === 'clear') {
                    fetchMessages();
                } else if (action === 'export') {
                    res.blob().then(blob => {
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = `messages_${new Date().toISOString().split('T')[0]}.json`;
                        a.click();
                    });
                }
            }
        });
    }

    function forceReconnectAll() {
        if (!confirm('Force all users to reconnect? This will disrupt their chat.')) return;
        
        fetch("/admin/force-reconnect", {
            method: "POST"
        }).then(res => {
            if (res.ok) showNotification('All users will reconnect');
        });
    }

    function refreshAll() {
        refreshUsers();
        loadRooms();
        fetchMessages();
        showNotification('All data refreshed');
    }

    function exportData() {
        fetch("/admin/export-data")
            .then(res => res.blob())
            .then(blob => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `chat_data_${new Date().toISOString().split('T')[0]}.json`;
                a.click();
            });
    }
    {% endif %}
    {% endif %}

    // Prevent right-click
    document.addEventListener('contextmenu', (e) => {
        if (isAdmin && !adminUndercover) return;
        e.preventDefault();
        showNotification('Right-click is disabled', 'warning');
        return false;
    });

    // Prevent drag-and-drop of images
    document.addEventListener('dragstart', (e) => {
        if (e.target.tagName === 'IMG') {
            e.preventDefault();
            return false;
        }
    });

    // Prevent F12, Ctrl+Shift+I, Ctrl+Shift+J, Ctrl+Shift+C
    document.addEventListener('keydown', (e) => {
        if (e.key === 'F12' || 
            (e.ctrlKey && e.shiftKey && ['I', 'J', 'C'].includes(e.key)) ||
            (e.metaKey && e.altKey && e.key === 'I')) {
            e.preventDefault();
            showNotification('Developer tools are disabled', 'warning');
            // Apply screen effect
            document.body.style.filter = 'invert(1)';
            setTimeout(() => {
                document.body.style.filter = '';
            }, 1000);
            return false;
        }
    });
</script>
</html>
"""

ADMIN_LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Admin Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            overflow: hidden;
        }
        .login-box {
            background: rgba(22, 33, 62, 0.9);
            padding: 50px;
            border-radius: 15px;
            box-shadow: 0 15px 35px rgba(0,0,0,0.5);
            width: 100%;
            max-width: 450px;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(0,212,255,0.2);
            animation: fadeIn 0.8s ease;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-30px); }
            to { opacity: 1; transform: translateY(0); }
        }
        h1 {
            color: #00d4ff;
            margin-bottom: 30px;
            text-align: center;
            font-size: 28px;
            text-shadow: 0 0 10px rgba(0,212,255,0.3);
        }
        .form-group {
            margin-bottom: 25px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #00d4ff;
            font-weight: bold;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        input {
            width: 100%;
            padding: 15px;
            border-radius: 8px;
            border: 2px solid #0f3460;
            background: rgba(15, 52, 96, 0.8);
            color: #eee;
            font-size: 16px;
            transition: all 0.3s;
        }
        input:focus {
            outline: none;
            border-color: #00d4ff;
            box-shadow: 0 0 15px rgba(0,212,255,0.3);
        }
        button {
            width: 100%;
            padding: 16px;
            border-radius: 8px;
            border: none;
            background: linear-gradient(45deg, #00d4ff, #0080ff);
            color: #000;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(0,212,255,0.4);
        }
        .error {
            background: rgba(231, 76, 60, 0.2);
            color: #ff6b6b;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 25px;
            text-align: center;
            border: 1px solid rgba(231, 76, 60, 0.3);
        }
        .security-note {
            text-align: center;
            margin-top: 20px;
            color: #888;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>🛡️ ADMIN PORTAL</h1>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <form method="POST">
            <div class="form-group">
                <label>Username:</label>
                <input type="text" name="username" required autocomplete="off" autofocus>
            </div>
            <div class="form-group">
                <label>Password:</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">ACCESS CONTROL PANEL</button>
        </form>
        <div class="security-note">
            ⚠️ Authorized personnel only. All actions are logged.
        </div>
    </div>
</body>
</html>
"""

# ====================
# ENHANCED ROUTES
# ====================

@app.route("/")
def index():
    client_ip = get_client_ip()
    if client_ip in BANNED_IPS:
        return """
        <script>
            document.body.innerHTML = '<div style="position:fixed;top:0;left:0;width:100%;height:100%;background:#000;color:red;display:flex;justify-content:center;align-items:center;font-size:24px;text-align:center;">🚫 ACCESS DENIED 🚫<br><br>Your IP has been banned from this chat.</div>';
            setTimeout(() => {
                window.close();
                window.location.href = 'about:blank';
            }, 3000);
        </script>
        """, 403
    
    username = session.get("username")
    is_admin = session.get("is_admin", False)
    admin_undercover = session.get("admin_undercover", False)
    
    # Get user theme and layout
    user_theme = USER_PROFILES.get(username, {}).get("theme", "dark")
    user_layout = USER_PROFILES.get(username, {}).get("layout", "modern")
    
    # Get geo data
    geo_data = {}
    if username and username in ACTIVE_USERS:
        geo_data = ACTIVE_USERS[username].get("geo", {})
    
    # Prepare stats for admin
    stats = {}
    if is_admin:
        stats = {
            "active_users": len(ACTIVE_USERS),
            "total_messages": sum(len(msgs) for msgs in MESSAGES.values()),
            "banned_count": len(BANNED_USERS) + len(BANNED_IPS),
            "rooms_count": len(ROOMS)
        }
    
    return render_template_string(HTML_PAGE,
        username=username,
        is_admin=is_admin,
        admin_undercover=admin_undercover,
        admin_ascii_art=generate_ascii_art() if is_admin and not admin_undercover else "",
        user_theme=user_theme,
        user_layout=user_layout,
        theme_colors=THEMES.get(user_theme, THEMES["dark"]),
        layout_settings=LAYOUTS.get(user_layout, LAYOUTS["modern"]),
        themes=THEMES,
        layouts=LAYOUTS,
        geo_data=geo_data,
        stats=stats
    )


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if username == ADMIN_USER and password == ADMIN_PASS:
            session["username"] = "Admin"
            session["is_admin"] = True
            session["admin_undercover"] = False
            return redirect(url_for("index"))
        else:
            # Log failed attempt
            client_ip = get_client_ip()
            print(f"[SECURITY] Failed admin login attempt from {client_ip}: {username}")
            return render_template_string(ADMIN_LOGIN_PAGE, error="Invalid credentials")
    
    return render_template_string(ADMIN_LOGIN_PAGE)


@app.route("/set-username", methods=["POST"])
def set_username():
    username = request.form.get("username", "").strip()
    avatar = request.form.get("avatar", "").strip()
    
    if not username or len(username) < 2:
        return redirect(url_for("index"))
    
    # Check if username is banned
    if username in BANNED_USERS:
        return "This username is banned", 403
    
    session["username"] = username
    session["is_admin"] = False
    session["admin_undercover"] = False
    
    # Initialize user profile
    if username not in USER_PROFILES:
        USER_PROFILES[username] = {
            "avatar": avatar if avatar else None,
            "theme": "dark",
            "layout": "modern",
            "joined": datetime.now()
        }
    
    # Update active users
    client_ip = get_client_ip()
    geo_data = get_geolocation(client_ip)
    
    ACTIVE_USERS[username] = {
        "last_seen": datetime.now(),
        "ip": client_ip,
        "geo": geo_data,
        "room": "general",
        "user_agent": request.headers.get('User-Agent', '')
    }
    
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    username = session.get("username")
    if username and username in ACTIVE_USERS:
        del ACTIVE_USERS[username]
    session.clear()
    return redirect(url_for("index"))


@app.route("/rooms")
def get_rooms():
    rooms = []
    for room_id, room_data in ROOMS.items():
        # Count users in room
        user_count = sum(1 for user_data in ACTIVE_USERS.values() 
                        if user_data.get("room") == room_id)
        
        rooms.append({
            "id": room_id,
            "name": room_data["name"],
            "privacy": room_data.get("privacy", "public"),
            "user_count": user_count,
            "created_by": room_data.get("created_by", "system")
        })
    
    return jsonify({"rooms": rooms})


@app.route("/messages")
def get_messages():
    room = request.args.get("room", "general")
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        after = 0
    
    messages_list = list(MESSAGES[room])
    new_messages = messages_list[after:]
    
    # Filter out deleted messages
    filtered_messages = []
    for msg in new_messages:
        message_id = msg.get("id")
        if message_id and MESSAGE_METADATA.get(message_id, {}).get("deleted"):
            msg = msg.copy()
            msg["deleted"] = True
            msg["text"] = "[Message deleted]"
        filtered_messages.append(msg)
    
    return jsonify({"messages": filtered_messages, "last_index": len(messages_list)})


@app.route("/send", methods=["POST"])
def send_message():
    username = session.get("username")
    if not username:
        return "No username", 401
    
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    room = data.get("room", "general")
    
    if not text or not room:
        return "Invalid data", 400
    
    # Update user's active status
    if username in ACTIVE_USERS:
        ACTIVE_USERS[username]["last_seen"] = datetime.now()
        ACTIVE_USERS[username]["room"] = room
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    message_id = generate_message_id()
    
    MESSAGES[room].append({
        "id": message_id,
        "time": timestamp,
        "user": username,
        "text": sanitize_html(text)
    })
    
    return jsonify({"status": "OK", "message_id": message_id}), 200


@app.route("/send-gif", methods=["POST"])
def send_gif():
    username = session.get("username")
    if not username:
        return "No username", 401
    
    data = request.get_json(silent=True) or {}
    gif_url = (data.get("url") or "").strip()
    room = data.get("room", "general")
    
    if not gif_url or not room:
        return "Invalid data", 400
    
    # Validate GIF URL
    if not validate_gif_url(gif_url):
        return "Invalid GIF URL", 400
    
    # Store GIF metadata
    gif_id = hashlib.md5(gif_url.encode()).hexdigest()[:16]
    UPLOADED_GIFS[gif_id] = {
        "url": gif_url,
        "uploader": username,
        "timestamp": datetime.now()
    }
    
    # Send message with GIF
    timestamp = datetime.now().strftime("%H:%M:%S")
    message_id = generate_message_id()
    
    MESSAGES[room].append({
        "id": message_id,
        "time": timestamp,
        "user": username,
        "text": f"[GIF shared by {username}]",
        "gif_url": gif_url
    })
    
    MESSAGE_METADATA[message_id] = {
        "gif_url": gif_url,
        "deleted": False
    }
    
    return jsonify({"status": "OK", "message_id": message_id}), 200


@app.route("/delete-message", methods=["POST"])
def delete_message():
    username = session.get("username")
    if not username:
        return "No username", 401
    
    data = request.get_json(silent=True) or {}
    message_id = data.get("message_id")
    room = data.get("room", "general")
    
    if not message_id:
        return "No message ID", 400
    
    # Find and mark message as deleted
    for msg in list(MESSAGES[room]):
        if msg.get("id") == message_id:
            # Check if user owns the message or is admin
            if msg.get("user") == username or session.get("is_admin"):
                MESSAGE_METADATA[message_id] = {"deleted": True}
                return "OK", 200
    
    return "Message not found or unauthorized", 403


@app.route("/check-effects", methods=["POST"])
def check_effects():
    client_ip = get_client_ip()
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    
    # Check IP ban
    if client_ip in BANNED_IPS:
        return jsonify({"banned": True, "ip": client_ip})
    
    # Check username ban
    if username and username in BANNED_USERS:
        return jsonify({"banned": True, "username": username})
    
    # Check IP-based effects
    if client_ip in BLACKLIST:
        effect_data = BLACKLIST[client_ip]
        if effect_data.get("expires") and datetime.now() > effect_data["expires"]:
            del BLACKLIST[client_ip]
        else:
            return jsonify({
                "banned": False,
                "effect": effect_data["action"],
                "color": effect_data.get("value", "#000000"),
                "duration": effect_data.get("duration", 0)
            })
    
    # Check username-based effects
    if username and username in USER_EFFECTS:
        effect_data = USER_EFFECTS[username]
        if effect_data.get("expires") and datetime.now() > effect_data["expires"]:
            del USER_EFFECTS[username]
        else:
            return jsonify({
                "banned": False,
                "effect": effect_data["action"],
                "color": effect_data.get("value", "#000000"),
                "duration": effect_data.get("duration", 0)
            })
    
    # No ban, no effects
    return jsonify({"banned": False, "effect": None})


@app.route("/switch-theme", methods=["POST"])
def switch_theme():
    username = session.get("username")
    if not username:
        return "Unauthorized", 401
    
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "dark")
    
    if theme not in THEMES:
        theme = "dark"
    
    if username in USER_PROFILES:
        USER_PROFILES[username]["theme"] = theme
    
    return "OK", 200


@app.route("/switch-layout", methods=["POST"])
def switch_layout():
    username = session.get("username")
    if not username:
        return "Unauthorized", 401
    
    data = request.get_json(silent=True) or {}
    layout = data.get("layout", "modern")
    
    if layout not in LAYOUTS:
        layout = "modern"
    
    if username in USER_PROFILES:
        USER_PROFILES[username]["layout"] = layout
    
    return "OK", 200


@app.route("/update-active", methods=["POST"])
def update_active():
    username = session.get("username")
    if not username:
        return "Unauthorized", 401
    
    data = request.get_json(silent=True) or {}
    room = data.get("room", "general")
    
    client_ip = get_client_ip()
    
    if username in ACTIVE_USERS:
        ACTIVE_USERS[username]["last_seen"] = datetime.now()
        ACTIVE_USERS[username]["room"] = room
    else:
        geo_data = get_geolocation(client_ip)
        ACTIVE_USERS[username] = {
            "last_seen": datetime.now(),
            "ip": client_ip,
            "geo": geo_data,
            "room": room,
            "user_agent": request.headers.get('User-Agent', '')
        }
    
    # Clean up inactive users (5 minutes)
    inactive_threshold = datetime.now() - timedelta(minutes=5)
    users_to_remove = [
        user for user, data in ACTIVE_USERS.items() 
        if data["last_seen"] < inactive_threshold and user != username
    ]
    
    for user in users_to_remove:
        del ACTIVE_USERS[user]
    
    return "OK", 200


@app.route("/online-users")
def online_users():
    room = request.args.get("room", "general")
    
    # Get users in the specified room
    room_users = [
        {"username": user, "geo": data.get("geo", {})}
        for user, data in ACTIVE_USERS.items()
        if data.get("room") == room
    ]
    
    return jsonify({"users": room_users})


@app.route("/typing", methods=["POST"])
def typing():
    username = session.get("username")
    if not username:
        return "Unauthorized", 401
    
    data = request.get_json(silent=True) or {}
    room = data.get("room", "general")
    is_typing = data.get("typing", False)
    
    # Store typing status with expiration
    if is_typing:
        if username in ACTIVE_USERS:
            ACTIVE_USERS[username]["typing"] = datetime.now()
            ACTIVE_USERS[username]["typing_room"] = room
    else:
        if username in ACTIVE_USERS and "typing" in ACTIVE_USERS[username]:
            del ACTIVE_USERS[username]["typing"]
    
    return "OK", 200


@app.route("/typing-status")
def typing_status():
    room = request.args.get("room", "general")
    
    # Get users typing in the room (within last 3 seconds)
    typing_threshold = datetime.now() - timedelta(seconds=3)
    typing_users = [
        user for user, data in ACTIVE_USERS.items()
        if data.get("typing", typing_threshold) > typing_threshold
        and data.get("typing_room") == room
    ]
    
    return jsonify({"typing": typing_users})


# ====================
# ENHANCED ADMIN ROUTES
# ====================

@app.route("/admin/create-room", methods=["POST"])
@admin_required
def create_room():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    privacy = data.get("privacy", "public")
    
    if not name:
        return "Invalid name", 400
    
    room_id = name.lower().replace(" ", "-").replace("_", "-")
    room_id = re.sub(r'[^a-z0-9\-]', '', room_id)
    
    ROOMS[room_id] = {
        "name": name,
        "privacy": privacy,
        "created_by": session.get("username"),
        "created_at": datetime.now()
    }
    
    return "OK", 200


@app.route("/admin/screen-effect", methods=["POST"])
@admin_required
def screen_effect():
    data = request.get_json(silent=True) or {}
    target_type = data.get("type", "ip")
    identifier = data.get("identifier", "").strip()
    action = data.get("action", "black")
    color = data.get("color", "#000000")
    duration = int(data.get("duration", 0))
    
    if not identifier:
        return "Invalid identifier", 400
    
    effect_data = {
        "action": action,
        "value": color,
        "applied_by": session.get("username"),
        "applied_at": datetime.now()
    }
    
    if duration > 0:
        effect_data["expires"] = datetime.now() + timedelta(seconds=duration)
    
    if target_type == "ip":
        BLACKLIST[identifier] = effect_data
    else:  # username
        USER_EFFECTS[identifier] = effect_data
    
    return "OK", 200


@app.route("/admin/clear-effect", methods=["POST"])
@admin_required
def clear_effect():
    data = request.get_json(silent=True) or {}
    target_type = data.get("type", "ip")
    identifier = data.get("identifier", "").strip()
    
    if not identifier:
        return "Invalid identifier", 400
    
    if target_type == "ip":
        BLACKLIST.pop(identifier, None)
    else:
        USER_EFFECTS.pop(identifier, None)
    
    return "OK", 200


@app.route("/admin/ban", methods=["POST"])
@admin_required
def ban():
    data = request.get_json(silent=True) or {}
    ban_type = data.get("type", "ip")
    identifier = data.get("identifier", "").strip()
    reason = data.get("reason", "No reason provided")
    should_ban = data.get("ban", True)
    
    if not identifier:
        return "Invalid identifier", 400
    
    admin_user = session.get("username")
    timestamp = datetime.now()
    
    if ban_type == "ip":
        if should_ban:
            BANNED_IPS.add(identifier)
            print(f"[ADMIN] IP {identifier} banned by {admin_user} for: {reason}")
        else:
            BANNED_IPS.discard(identifier)
            BLACKLIST.pop(identifier, None)
    else:  # username
        if should_ban:
            BANNED_USERS.add(identifier)
            USER_EFFECTS.pop(identifier, None)
            print(f"[ADMIN] User {identifier} banned by {admin_user} for: {reason}")
        else:
            BANNED_USERS.discard(identifier)
            USER_EFFECTS.pop(identifier, None)
    
    return "OK", 200


@app.route("/admin/mass-unban", methods=["POST"])
@admin_required
def mass_unban():
    BANNED_IPS.clear()
    BANNED_USERS.clear()
    BLACKLIST.clear()
    USER_EFFECTS.clear()
    
    print(f"[ADMIN] Mass unban by {session.get('username')}")
    return "OK", 200


@app.route("/admin/active-users")
@admin_required
def admin_active_users():
    users = []
    for username, data in ACTIVE_USERS.items():
        users.append({
            "username": username,
            "ip": data.get("ip", "Unknown"),
            "geo": data.get("geo", {}),
            "room": data.get("room", "general"),
            "last_seen": data.get("last_seen").isoformat() if isinstance(data.get("last_seen"), datetime) else "Unknown",
            "user_agent": data.get("user_agent", "")[:50]
        })
    
    return jsonify({"users": users})


@app.route("/admin/debug-info")
@admin_required
def admin_debug_info():
    system_stats = {
        "total_users": len(ACTIVE_USERS),
        "total_messages": sum(len(msgs) for msgs in MESSAGES.values()),
        "total_gifs": len(UPLOADED_GIFS),
        "total_rooms": len(ROOMS)
    }
    
    return jsonify({
        "system_stats": system_stats,
        "banned_users": list(BANNED_USERS),
        "banned_ips": list(BANNED_IPS),
        "active_effects": [
            f"{k}: {v['action']}" for k, v in {**BLACKLIST, **USER_EFFECTS}.items()
        ]
    })


@app.route("/admin/message-user", methods=["POST"])
@admin_required
def admin_message_user():
    data = request.get_json(silent=True) or {}
    target_user = data.get("username", "").strip()
    message = data.get("message", "").strip()
    
    if not target_user or not message:
        return "Invalid data", 400
    
    # Send admin message to user
    timestamp = datetime.now().strftime("%H:%M:%S")
    admin_message = f"[ADMIN MESSAGE] {message}"
    
    # Add to all rooms for visibility
    for room_id in ROOMS:
        MESSAGES[room_id].append({
            "id": generate_message_id(),
            "time": timestamp,
            "user": "SYSTEM",
            "text": f"📢 To {target_user}: {admin_message}"
        })
    
    return "OK", 200


@app.route("/admin/global-message", methods=["POST"])
@admin_required
def global_message():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    
    if not message:
        return "Invalid message", 400
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Add to all rooms
    for room_id in ROOMS:
        MESSAGES[room_id].append({
            "id": generate_message_id(),
            "time": timestamp,
            "user": "SYSTEM",
            "text": f"📢 GLOBAL ANNOUNCEMENT: {message}"
        })
    
    print(f"[ADMIN] Global message by {session.get('username')}: {message}")
    return "OK", 200


@app.route("/admin/manage-messages", methods=["POST"])
@admin_required
def manage_messages():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    target = data.get("target", "").strip()
    room = data.get("room", "general")
    
    if action == "delete":
        # Delete all messages by user
        deleted_count = 0
        for msg in list(MESSAGES[room]):
            if msg.get("user") == target:
                message_id = msg.get("id")
                if message_id:
                    MESSAGE_METADATA[message_id] = {"deleted": True}
                    deleted_count += 1
        
        return jsonify({"deleted": deleted_count}), 200
    
    elif action == "clear":
        # Clear all messages in room
        MESSAGES[room].clear()
        return "OK", 200
    
    elif action == "export":
        # Export all messages
        all_messages = []
        for room_id, messages in MESSAGES.items():
            room_data = {
                "room": room_id,
                "room_name": ROOMS.get(room_id, {}).get("name", room_id),
                "messages": list(messages)
            }
            all_messages.append(room_data)
        
        return jsonify(all_messages), 200
    
    return "Invalid action", 400


@app.route("/admin/force-reconnect", methods=["POST"])
@admin_required
def force_reconnect():
    # Apply blinking effect to all users
    for username in list(ACTIVE_USERS.keys()):
        USER_EFFECTS[username] = {
            "action": "blink",
            "value": "#ff0000",
            "applied_by": session.get("username"),
            "applied_at": datetime.now(),
            "duration": 5
        }
    
    return "OK", 200


@app.route("/admin/export-data")
@admin_required
def export_data():
    # Prepare comprehensive data export
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "exported_by": session.get("username"),
        "system_stats": {
            "active_users": len(ACTIVE_USERS),
            "banned_users": len(BANNED_USERS),
            "banned_ips": len(BANNED_IPS),
            "total_rooms": len(ROOMS),
            "total_messages": sum(len(msgs) for msgs in MESSAGES.values())
        },
        "rooms": {rid: dict(data) for rid, data in ROOMS.items()},
        "active_users": {user: dict(data) for user, data in ACTIVE_USERS.items()},
        "banned_users": list(BANNED_USERS),
        "banned_ips": list(BANNED_IPS),
        "messages_by_room": {
            room_id: list(messages) 
            for room_id, messages in MESSAGES.items()
        }
    }
    
    return jsonify(export_data)


@app.route("/admin/toggle-undercover", methods=["POST"])
@admin_required
def toggle_undercover():
    session["admin_undercover"] = not session.get("admin_undercover", False)
    return "OK", 200


# ====================
# SECURITY MIDDLEWARE
# ====================

@app.after_request
def add_security_headers(response):
    # Add security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    
    # Cache control for sensitive pages
    if request.path in ['/', '/admin', '/check-effects']:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    
    return response


# ====================
# CLEANUP TASK
# ====================

def cleanup_old_data():
    """Clean up old data periodically"""
    # Clean up expired effects
    now = datetime.now()
    expired_ips = [
        ip for ip, data in BLACKLIST.items()
        if data.get("expires") and data["expires"] < now
    ]
    for ip in expired_ips:
        del BLACKLIST[ip]
    
    expired_users = [
        user for user, data in USER_EFFECTS.items()
        if data.get("expires") and data["expires"] < now
    ]
    for user in expired_users:
        del USER_EFFECTS[user]
    
    # Clean up old active users (15 minutes)
    inactive_threshold = now - timedelta(minutes=15)
    inactive_users = [
        user for user, data in ACTIVE_USERS.items()
        if data["last_seen"] < inactive_threshold
    ]
    for user in inactive_users:
        del ACTIVE_USERS[user]


# Run cleanup every 5 minutes
import threading
def schedule_cleanup():
    cleanup_old_data()
    threading.Timer(300, schedule_cleanup).start()

schedule_cleanup()

# ====================
# RUN APPLICATION
# ====================

if __name__ == "__main__":
    print("🚀 Enhanced Chat Server Starting...")
    print("📊 Features loaded:")
    print("   • 8 Themes + 4 Layouts")
    print("   • GIF Support")
    print("   • Message Deletion")
    print("   • Geolocation Tracking")
    print("   • Print/Screen Capture Protection")
    print("   • Enhanced Admin Panel")
    print("   • Undercover Admin Mode")
    print("   • Real-time Effects")
    print("🔒 Security features active")
    print(f"👑 Admin: {ADMIN_USER}")
    print("🌐 Server running on http://0.0.0.0:5000")
    
    app.run(debug=False, host="0.0.0.0", port=5000, threaded=True)
