import os
import re
import unicodedata
import hashlib
from datetime import datetime, timedelta
from contextlib import contextmanager

# Neon Cloud Database URL (Két sắt dữ liệu đám mây an toàn vĩnh viễn)
NEON_DB_URL = 'postgresql://neondb_owner:npg_1jfDzTkK2YiE@ep-rough-star-ae2vh7hc-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require'
DATABASE_URL = os.environ.get('DATABASE_URL', NEON_DB_URL)

USE_POSTGRES = False
try:
    if DATABASE_URL and DATABASE_URL.startswith(('postgres://', 'postgresql://')):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        USE_POSTGRES = True
except Exception:
    USE_POSTGRES = False

if not USE_POSTGRES:
    import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tracker.db')

def get_db():
    if USE_POSTGRES:
        url = DATABASE_URL
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

@contextmanager
def db_session():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def q(sql):
    """Chuyển đổi dấu ? thành %s nếu đang dùng PostgreSQL"""
    if USE_POSTGRES:
        return sql.replace('?', '%s')
    return sql

def init_db():
    with db_session() as conn:
        c = conn.cursor()
        if USE_POSTGRES:
            c.execute('''
                CREATE TABLE IF NOT EXISTS links (
                    id SERIAL PRIMARY KEY,
                    slug VARCHAR(255) UNIQUE NOT NULL,
                    channel_name VARCHAR(255) NOT NULL,
                    channel_group VARCHAR(50) DEFAULT 'Nội bộ',
                    platform VARCHAR(50) DEFAULT 'TikTok',
                    destination_url TEXT NOT NULL,
                    created_at VARCHAR(50) NOT NULL
                );
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS clicks (
                    id SERIAL PRIMARY KEY,
                    link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
                    clicked_at VARCHAR(50) NOT NULL,
                    device_type VARCHAR(50) DEFAULT 'Unknown',
                    ip_hash VARCHAR(64),
                    user_agent TEXT,
                    referrer TEXT
                );
            ''')
        else:
            c.execute('''
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE NOT NULL,
                    channel_name TEXT NOT NULL,
                    channel_group TEXT DEFAULT 'Nội bộ',
                    platform TEXT DEFAULT 'TikTok',
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
            
            # Cập nhật cột nếu dùng SQLite cũ
            c.execute("PRAGMA table_info(links)")
            cols = [r['name'] for r in c.fetchall()]
            if 'channel_group' not in cols:
                c.execute("ALTER TABLE links ADD COLUMN channel_group TEXT DEFAULT 'Nội bộ'")
            if 'platform' not in cols:
                c.execute("ALTER TABLE links ADD COLUMN platform TEXT DEFAULT 'TikTok'")

        c.execute('CREATE INDEX IF NOT EXISTS idx_clicks_time ON clicks (clicked_at);')
        c.execute('CREATE INDEX IF NOT EXISTS idx_clicks_link ON clicks (link_id, clicked_at);')
        c.execute('CREATE INDEX IF NOT EXISTS idx_links_slug ON links (slug);')
        c.execute('CREATE INDEX IF NOT EXISTS idx_links_group ON links (channel_group);')

def slugify(text, group='Nội bộ', platform='TikTok'):
    text = text.replace('đ', 'd').replace('Đ', 'd')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    
    # Chuẩn hóa tiền tố nền tảng
    p = platform.lower().strip()
    if 'tik' in p:
        p_prefix = 'tiktok'
    elif 'face' in p or 'fb' in p:
        p_prefix = 'fb'
    elif 'you' in p or 'yt' in p:
        p_prefix = 'yt'
    elif 'thread' in p:
        p_prefix = 'threads'
    else:
        p_prefix = re.sub(r'[^a-z0-9]+', '', p) or 'link'
        
    # Chuẩn hóa tiền tố nhóm
    is_kol = (group.upper() in ['KOL', 'KOC'])
    g_prefix = 'kol-' if is_kol else ''
    
    # Loại bỏ @ hoặc các ký tự lạ ở tên kênh
    clean_name = re.sub(r'[^a-z0-9]+', '-', text.replace('@', '')).strip('-')
    if not clean_name:
        clean_name = 'kenh'
        
    slug = f"{p_prefix}-{g_prefix}{clean_name}".replace('--', '-')
    return slug

def create_link(channel_name, destination_url, custom_slug=None, channel_group='Nội bộ', platform='TikTok'):
    if not destination_url.startswith(('http://', 'https://')):
        destination_url = 'https://' + destination_url
        
    group = 'KOL' if str(channel_group).strip().upper() in ['KOL', 'KOC'] else 'Nội bộ'
    plat = platform.strip() if platform and platform.strip() else 'TikTok'
    
    if custom_slug and custom_slug.strip():
        base_slug = re.sub(r'[^a-z0-9\-]+', '-', custom_slug.lower().strip()).strip('-')
    else:
        base_slug = slugify(channel_name, group=group, platform=plat)
        
    slug = base_slug
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    with db_session() as conn:
        c = conn.cursor()
        counter = 1
        while True:
            try:
                if USE_POSTGRES:
                    c.execute(
                        'INSERT INTO links (slug, channel_name, channel_group, platform, destination_url, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                        (slug, channel_name, group, plat, destination_url, now)
                    )
                    new_id = c.fetchone()['id']
                else:
                    c.execute(
                        'INSERT INTO links (slug, channel_name, channel_group, platform, destination_url, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (slug, channel_name, group, plat, destination_url, now)
                    )
                    new_id = c.lastrowid
                    
                return {
                    'id': new_id,
                    'slug': slug,
                    'channel_name': channel_name,
                    'channel_group': group,
                    'platform': plat,
                    'destination_url': destination_url,
                    'created_at': now
                }
            except Exception as e:
                # Bắt lỗi trùng lặp slug (IntegrityError)
                conn.rollback()
                counter += 1
                slug = f"{base_slug}-{counter}"
                if counter > 100:
                    raise e

def get_all_links():
    with db_session() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT l.id, l.slug, l.channel_name, l.channel_group, l.platform, l.destination_url, l.created_at,
                   COUNT(c.id) as total_clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_clicks,
                   MAX(c.clicked_at) as last_clicked
            FROM links l
            LEFT JOIN clicks c ON l.id = c.link_id
            GROUP BY l.id, l.slug, l.channel_name, l.channel_group, l.platform, l.destination_url, l.created_at
            ORDER BY l.created_at DESC
        ''')
        return [dict(r) for r in c.fetchall()]

def get_link_by_slug(slug):
    with db_session() as conn:
        c = conn.cursor()
        c.execute(q('SELECT * FROM links WHERE slug = ?'), (slug,))
        r = c.fetchone()
        return dict(r) if r else None

def record_click(slug, device_type='Unknown', ip_address='', user_agent='', referrer=''):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fingerprint_source = f"{ip_address}_{user_agent}_{device_type}"
    ip_hash = hashlib.sha256(fingerprint_source.encode('utf-8')).hexdigest()[:16] if ip_address else 'anonymous'
    
    with db_session() as conn:
        c = conn.cursor()
        c.execute(q('SELECT id, destination_url FROM links WHERE slug = ?'), (slug,))
        link = c.fetchone()
        if not link:
            return None
        
        link_id = link['id']
        destination_url = link['destination_url']
        
        c.execute(q('''
            INSERT INTO clicks (link_id, clicked_at, device_type, ip_hash, user_agent, referrer)
            VALUES (?, ?, ?, ?, ?, ?)
        '''), (link_id, now, device_type, ip_hash, user_agent, referrer))
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
    
    with db_session() as conn:
        c = conn.cursor()
        
        # 1. Thống kê theo từng nhóm
        c.execute("SELECT channel_group, COUNT(*) as count FROM links GROUP BY channel_group")
        link_counts = {r['channel_group']: r['count'] for r in c.fetchall()}
        
        c.execute(q(f'''
            SELECT l.channel_group,
                   COUNT(c.id) as clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_visitors
            FROM links l
            LEFT JOIN clicks c ON l.id = c.link_id AND {where_sql}
            GROUP BY l.channel_group
        '''), params)
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
                
        # Lọc nhóm nếu người dùng chọn tab
        channel_where = where_sql
        channel_params = list(params)
        if group_filter and group_filter in ['Nội bộ', 'KOL']:
            channel_where += ' AND l.channel_group = ?'
            channel_params.append(group_filter)
            
        # 2. Tổng quan
        c.execute(q(f'''
            SELECT COUNT(c.id) as total_clicks,
                   COUNT(DISTINCT c.ip_hash) as unique_visitors
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {channel_where}
        '''), channel_params)
        ov = c.fetchone()
        overview = dict(ov) if ov else {'total_clicks': 0, 'unique_visitors': 0}
        
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
            
        c.execute(q(f'''
            SELECT COUNT(c.id) as today_clicks
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {today_where}
        '''), today_params)
        td = c.fetchone()
        overview['today_clicks'] = td['today_clicks'] if td else 0
        
        # 3. Danh sách chi tiết các kênh
        channel_query = f'''
            SELECT l.id, l.slug, l.channel_name, l.channel_group, l.platform, l.destination_url,
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
            
        channel_query += ' GROUP BY l.id, l.slug, l.channel_name, l.channel_group, l.platform, l.destination_url ORDER BY clicks DESC, l.created_at DESC'
        c.execute(q(channel_query), table_params)
        channel_rows = [dict(r) for r in c.fetchall()]
        
        total_clicks = overview.get('total_clicks') or 0
        for ch in channel_rows:
            ch['percentage'] = round((ch['clicks'] / total_clicks * 100), 1) if total_clicks > 0 else 0
            
        overview['top_channel'] = channel_rows[0]['channel_name'] if (channel_rows and channel_rows[0]['clicks'] > 0) else 'Chưa có'
        
        # 4. Biểu đồ thời gian (dùng substr để tương thích 100% cả SQLite và PostgreSQL)
        is_same_day = (start_date and end_date and start_date[:10] == end_date[:10])
        time_expr = "substr(c.clicked_at, 1, 13) || ':00'" if is_same_day else "substr(c.clicked_at, 1, 10)"
        
        c.execute(q(f'''
            SELECT {time_expr} as time_label,
                   l.channel_group,
                   COUNT(c.id) as clicks
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            WHERE {channel_where}
            GROUP BY time_label, l.channel_group
            ORDER BY time_label ASC
        '''), channel_params)
        time_rows = [dict(r) for r in c.fetchall()]
        
        return {
            'overview': overview,
            'groups': groups_stat,
            'channels': channel_rows,
            'timeseries': time_rows
        }

def delete_link(slug):
    with db_session() as conn:
        c = conn.cursor()
        c.execute(q('DELETE FROM links WHERE slug = ?'), (slug,))

def delete_links_batch(slug_list):
    if not slug_list:
        return 0
    with db_session() as conn:
        c = conn.cursor()
        placeholders = ','.join(['?'] * len(slug_list))
        c.execute(q(f'DELETE FROM links WHERE slug IN ({placeholders})'), slug_list)
        return c.rowcount

def clear_all_clicks():
    with db_session() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM clicks')
