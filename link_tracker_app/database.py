import sqlite3
import os
import re
import unicodedata
import hashlib
import random
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tracker.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                channel_name TEXT NOT NULL,
                channel_group TEXT DEFAULT 'Nội bộ',
                destination_url TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id INTEGER NOT NULL,
                clicked_at TEXT NOT NULL,
                device_type TEXT DEFAULT 'Unknown',
                ip_hash TEXT,
                user_agent TEXT,
                referrer TEXT,
                FOREIGN KEY (link_id) REFERENCES links(id) ON DELETE CASCADE
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_clicks_time ON clicks (clicked_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_clicks_link ON clicks (link_id, clicked_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_links_slug ON links (slug)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_links_group ON links (channel_group)')
        
        c.execute("PRAGMA table_info(links)")
        cols = [r['name'] for r in c.fetchall()]
        if 'channel_group' not in cols:
            c.execute("ALTER TABLE links ADD COLUMN channel_group TEXT DEFAULT 'Nội bộ'")
            
        conn.commit()

def slugify(text, group='Nội bộ'):
    text = text.replace('đ', 'd').replace('Đ', 'd')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    
    if group == 'KOL':
        if not text.startswith('kol-') and not text.startswith('koc-'):
            text = 'kol-' + text.replace('@', '')
    else:
        if not text.startswith('tiktok') and not text.startswith('tt-'):
            text = 'tiktok-' + text.replace('@', '')
            
    text = re.sub(r'[^a-z0-9]+', '-', text).strip('-')
    return text if text else 'link-tiktok'

def create_link(channel_name, destination_url, custom_slug=None, channel_group='Nội bộ'):
    if not destination_url.startswith(('http://', 'https://')):
        destination_url = 'https://' + destination_url
        
    group = 'KOL' if str(channel_group).strip().upper() in ['KOL', 'KOC'] else 'Nội bộ'
    
    if custom_slug and custom_slug.strip():
        base_slug = re.sub(r'[^a-z0-9\-]+', '-', custom_slug.lower().strip()).strip('-')
    else:
        base_slug = slugify(channel_name, group)
        
    slug = base_slug
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    with get_db() as conn:
        c = conn.cursor()
        counter = 1
        while True:
            try:
                c.execute(
                    'INSERT INTO links (slug, channel_name, channel_group, destination_url, created_at) VALUES (?, ?, ?, ?, ?)',
                    (slug, channel_name, group, destination_url, now)
                )
                conn.commit()
                return {
                    'id': c.lastrowid,
                    'slug': slug,
                    'channel_name': channel_name,
                    'channel_group': group,
                    'destination_url': destination_url,
                    'created_at': now
                }
            except sqlite3.IntegrityError:
                counter += 1
                slug = f"{base_slug}-{counter}"

def get_all_links():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT l.id, l.slug, l.channel_name, l.channel_group, l.destination_url, l.created_at,
                   COUNT(c.id) as total_clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_clicks,
                   MAX(c.clicked_at) as last_clicked
            FROM links l
            LEFT JOIN clicks c ON l.id = c.link_id
            GROUP BY l.id
            ORDER BY l.created_at DESC
        ''')
        return [dict(r) for r in c.fetchall()]

def get_link_by_slug(slug):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM links WHERE slug = ?', (slug,))
        r = c.fetchone()
        return dict(r) if r else None

def record_click(slug, device_type='Unknown', ip_address='', user_agent='', referrer=''):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fingerprint_source = f"{ip_address}_{user_agent}_{device_type}"
    ip_hash = hashlib.sha256(fingerprint_source.encode('utf-8')).hexdigest()[:16] if ip_address else 'anonymous'
    
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT id, destination_url FROM links WHERE slug = ?', (slug,))
        link = c.fetchone()
        if not link:
            return None
        
        link_id = link['id']
        destination_url = link['destination_url']
        
        c.execute('''
            INSERT INTO clicks (link_id, clicked_at, device_type, ip_hash, user_agent, referrer)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (link_id, now, device_type, ip_hash, user_agent, referrer))
        conn.commit()
        return destination_url

def get_analytics(start_date=None, end_date=None, group_filter=None):
    params = []
    where_clauses = ['1=1']
    
    if start_date:
        if len(start_date) == 10:
            start_date += ' 00:00:00'
        where_clauses.append('c.clicked_at >= ?')
        params.append(start_date)
        
    if end_date:
        if len(end_date) == 10:
            end_date += ' 23:59:59'
        where_clauses.append('c.clicked_at <= ?')
        params.append(end_date)
        
    where_sql = ' AND '.join(where_clauses)
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    with get_db() as conn:
        c = conn.cursor()
        
        # 1. Thống kê theo từng nhóm (Nội bộ vs KOL)
        c.execute("SELECT channel_group, COUNT(*) as count FROM links GROUP BY channel_group")
        link_counts = {r['channel_group']: r['count'] for r in c.fetchall()}
        
        c.execute(f'''
            SELECT l.channel_group,
                   COUNT(c.id) as clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_visitors
            FROM links l
            LEFT JOIN clicks c ON l.id = c.link_id AND {where_sql}
            GROUP BY l.channel_group
        ''', params)
        group_rows = [dict(r) for r in c.fetchall()]
        
        groups_stat = {
            'Nội bộ': {'clicks': 0, 'unique_visitors': 0, 'total_links': link_counts.get('Nội bộ', 0), 'percentage': 0},
            'KOL': {'clicks': 0, 'unique_visitors': 0, 'total_links': link_counts.get('KOL', 0), 'percentage': 0}
        }
        for g in group_rows:
            grp_name = g['channel_group'] or 'Nội bộ'
            if grp_name in groups_stat:
                groups_stat[grp_name]['clicks'] = g['clicks']
                groups_stat[grp_name]['unique_visitors'] = g['unique_visitors']
                
        # Thêm điều kiện lọc nhóm nếu người dùng chọn tab cụ thể
        channel_where = where_sql
        channel_params = list(params)
        if group_filter and group_filter in ['Nội bộ', 'KOL']:
            channel_where += ' AND l.channel_group = ?'
            channel_params.append(group_filter)
            
        # 2. Tổng quan
        c.execute(f'''
            SELECT COUNT(c.id) as total_clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_visitors
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {channel_where}
        ''', channel_params)
        ov = c.fetchone()
        overview = dict(ov) if ov else {'total_clicks': 0, 'unique_visitors': 0}
        
        # Tính % giữa 2 nhóm
        overall_total = (groups_stat['Nội bộ']['clicks'] + groups_stat['KOL']['clicks'])
        if overall_total > 0:
            groups_stat['Nội bộ']['percentage'] = round(groups_stat['Nội bộ']['clicks'] / overall_total * 100, 1)
            groups_stat['KOL']['percentage'] = round(groups_stat['KOL']['clicks'] / overall_total * 100, 1)
            
        # Click hôm nay
        today_where = 'c.clicked_at >= ?'
        today_params = [f"{today_str} 00:00:00"]
        if group_filter and group_filter in ['Nội bộ', 'KOL']:
            today_where += ' AND l.channel_group = ?'
            today_params.append(group_filter)
            
        c.execute(f'''
            SELECT COUNT(c.id) as today_clicks
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {today_where}
        ''', today_params)
        td = c.fetchone()
        overview['today_clicks'] = td['today_clicks'] if td else 0
        
        # 3. Danh sách chi tiết các kênh (ĐÃ FIX TOÀN DIỆN BINDING THAM SỐ)
        channel_query = f'''
            SELECT l.id, l.slug, l.channel_name, l.channel_group, l.destination_url,
                   COUNT(c.id) as clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_clicks,
                   MAX(c.clicked_at) as last_click
            FROM links l
            LEFT JOIN clicks c ON l.id = c.link_id AND {where_sql}
        '''
        table_params = list(params)
        if group_filter in ['Nội bộ', 'KOL']:
            channel_query += ' WHERE l.channel_group = ?'
            table_params.append(group_filter)
            
        channel_query += ' GROUP BY l.id ORDER BY clicks DESC, l.created_at DESC'
        
        c.execute(channel_query, table_params)
        channel_rows = [dict(r) for r in c.fetchall()]
        
        total_clicks = overview.get('total_clicks') or 0
        for ch in channel_rows:
            ch['percentage'] = round((ch['clicks'] / total_clicks * 100), 1) if total_clicks > 0 else 0
            
        overview['top_channel'] = channel_rows[0]['channel_name'] if (channel_rows and channel_rows[0]['clicks'] > 0) else 'Chưa có'
        
        # 4. Biểu đồ thời gian
        is_same_day = (start_date and end_date and start_date[:10] == end_date[:10])
        group_format = '%Y-%m-%d %H:00' if is_same_day else '%Y-%m-%d'
        
        c.execute(f'''
            SELECT strftime('{group_format}', c.clicked_at) as time_label,
                   l.channel_group,
                   COUNT(c.id) as clicks
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {channel_where}
            GROUP BY time_label, l.channel_group
            ORDER BY time_label ASC
        ''', channel_params)
        time_rows = [dict(r) for r in c.fetchall()]
        
        return {
            'overview': overview,
            'groups': groups_stat,
            'channels': channel_rows,
            'timeseries': time_rows
        }

def delete_link(slug):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM links WHERE slug = ?', (slug,))
        conn.commit()

def clear_all_clicks():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM clicks')
        conn.commit()

def seed_demo_data():
    init_db()
    existing_links = get_all_links()
    
    if existing_links:
        target_links = existing_links
    else:
        sample_channels = [
            ('TikTok Kênh Chính', 'https://my-shop.vn', 'Nội bộ'),
            ('TikTok Kênh Phụ 1', 'https://my-shop.vn', 'Nội bộ'),
            ('TikTok Bio Link', 'https://my-shop.vn', 'Nội bộ'),
            ('TikTok Livestream', 'https://my-shop.vn', 'Nội bộ'),
            ('KOL Mai Anh Review', 'https://my-shop.vn', 'KOL'),
            ('KOL Hoàng Long KOC', 'https://my-shop.vn', 'KOL'),
            ('KOL Linh Chi Beauty', 'https://my-shop.vn', 'KOL')
        ]
        target_links = []
        for name, url, grp in sample_channels:
            link = create_link(name, url, channel_group=grp)
            target_links.append(link)
            
    clear_all_clicks()
    
    devices = ['Điện thoại', 'Điện thoại', 'Điện thoại', 'Máy tính']
    now = datetime.now()
    
    # Tạo đủ lượt click để phân bổ đều cho tất cả kênh (kể cả khi có > 50-100 kênh)
    total_clicks_to_gen = max(350, len(target_links) * 15)
    click_records = []
    for _ in range(total_clicks_to_gen):
        link = random.choice(target_links)
        days_ago = random.randint(0, 13)
        hours_ago = random.randint(0, 23)
        minutes_ago = random.randint(0, 59)
        clicked_time = (now - timedelta(days=days_ago, hours=hours_ago, minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S')
        device = random.choice(devices)
        ip_hash = f"user_{random.randint(1, 150)}"
        click_records.append((link['id'], clicked_time, device, ip_hash, 'Mozilla/5.0 (iPhone; CPU OS)', 'https://www.tiktok.com/'))
        
    with get_db() as conn:
        c = conn.cursor()
        c.executemany('''
            INSERT INTO clicks (link_id, clicked_at, device_type, ip_hash, user_agent, referrer)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', click_records)
        conn.commit()
    return len(target_links)
