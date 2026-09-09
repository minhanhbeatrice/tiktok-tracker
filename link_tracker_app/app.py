import os
import csv
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, jsonify, Response
import database as db

app = Flask(__name__)
db.init_db()

def detect_device(user_agent_str):
    ua = user_agent_str.lower()
    if 'ipad' in ua or 'tablet' in ua:
        return 'Máy tính bảng'
    elif 'mobile' in ua or 'android' in ua or 'iphone' in ua:
        return 'Điện thoại'
    return 'Máy tính'

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/r/<slug>')
def track_and_redirect(slug):
    ua = request.headers.get('User-Agent', '')
    device = detect_device(ua)
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    if ',' in ip:
        ip = ip.split(',')[0].strip()
    referrer = request.referrer or 'Trực tiếp'
    
    destination = db.record_click(slug, device_type=device, ip_address=ip, user_agent=ua, referrer=referrer)
    if destination:
        return redirect(destination, code=302)
    return """
    <div style="font-family:sans-serif; text-align:center; padding:50px;">
        <h2>Liên kết không tồn tại hoặc đã bị gỡ</h2>
        <p>Vui lòng kiểm tra lại đường dẫn.</p>
        <a href="/" style="color:#000000; font-weight:bold;">Về trang quản lý</a>
    </div>
    """, 404

@app.route('/api/links', methods=['GET'])
def list_links():
    links = db.get_all_links()
    base_url = request.host_url.rstrip('/')
    for link in links:
        link['tracking_url'] = f"{base_url}/r/{link['slug']}"
    return jsonify({'success': True, 'links': links})

@app.route('/api/links', methods=['POST'])
def create_links():
    data = request.get_json() or {}
    dest_url = data.get('destination_url', '').strip()
    channels = data.get('channels', [])
    channel_group = data.get('channel_group', 'Nội bộ')
    platform = data.get('platform', 'TikTok')
    
    if not dest_url:
        return jsonify({'success': False, 'message': 'Vui lòng nhập link website đích!'}), 400
        
    if not channels:
        return jsonify({'success': False, 'message': 'Vui lòng nhập danh sách kênh!'}), 400
        
    base_url = request.host_url.rstrip('/')
    created = []
    
    for item in channels:
        if isinstance(item, dict):
            name = item.get('name', '').strip()
            custom_slug = item.get('slug', '').strip()
            grp = item.get('group', channel_group)
            plat = item.get('platform', platform)
        else:
            name = str(item).strip()
            custom_slug = None
            grp = channel_group
            plat = platform
            
        if name:
            new_link = db.create_link(name, dest_url, custom_slug=custom_slug, channel_group=grp, platform=plat)
            new_link['tracking_url'] = f"{base_url}/r/{new_link['slug']}"
            created.append(new_link)
        
    return jsonify({'success': True, 'created': created})

@app.route('/api/links/<slug>', methods=['DELETE'])
def delete_link(slug):
    db.delete_link(slug)
    return jsonify({'success': True, 'message': f'Đã xóa link {slug}'})

@app.route('/api/links/batch-delete', methods=['POST'])
def batch_delete_links():
    data = request.get_json() or {}
    slugs = data.get('slugs', [])
    if not slugs:
        return jsonify({'success': False, 'message': 'Chưa chọn link nào để xóa!'}), 400
        
    count = db.delete_links_batch(slugs)
    return jsonify({'success': True, 'message': f'Đã xóa thành công {count} link!'})

@app.route('/api/analytics', methods=['GET'])
def get_analytics():
    try:
        range_type = request.args.get('range', '7d')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        group_filter = request.args.get('group', 'all')
        
        now = datetime.now()
        if range_type == 'today':
            start_date = now.strftime('%Y-%m-%d')
            end_date = now.strftime('%Y-%m-%d')
        elif range_type == 'yesterday':
            yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
            start_date = yesterday
            end_date = yesterday
        elif range_type == '7d':
            start_date = (now - timedelta(days=6)).strftime('%Y-%m-%d')
            end_date = now.strftime('%Y-%m-%d')
        elif range_type == '30d':
            start_date = (now - timedelta(days=29)).strftime('%Y-%m-%d')
            end_date = now.strftime('%Y-%m-%d')
        elif range_type == 'all':
            start_date = None
            end_date = None
            
        grp = group_filter if group_filter in ['Nội bộ', 'KOL'] else None
        data = db.get_analytics(start_date=start_date, end_date=end_date, group_filter=grp)
        data['filter_applied'] = {
            'range': range_type,
            'start_date': start_date,
            'end_date': end_date,
            'group': group_filter
        }
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/export-csv', methods=['GET'])
def export_csv():
    with db.get_db() as conn:
        c = conn.cursor()
        query = '''
            SELECT c.clicked_at, l.platform, l.channel_group, l.channel_name, l.slug, l.destination_url, c.device_type, c.referrer
            FROM clicks c
            JOIN links l ON c.link_id = l.id
            ORDER BY c.clicked_at DESC
        '''
        c.execute(query)
        rows = c.fetchall()
        
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['ThoiGian', 'NenTang', 'NhomKenh', 'TenKenh', 'MaLinkSlug', 'LinkDich', 'ThietBi', 'Nguon'])
    for r in rows:
        cw.writerow([r['clicked_at'], r['platform'], r['channel_group'], r['channel_name'], r['slug'], r['destination_url'], r['device_type'], r['referrer']])
        
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=bao-cao-traffic.csv"}
    )

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
