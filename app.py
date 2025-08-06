from flask import Flask, request, render_template_string, jsonify, Response, send_file
import csv
import os
import time
import json
from threading import Lock
from io import StringIO
import re
import logging
import hashlib

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Thread-safe seating map
seating_map = {}
lock = Lock()
last_data_hash = None

# Check if DSC_3378.jpg exists
image_path = os.path.join('static', 'back.png')
background_image = '/static/back.png' if os.path.exists(image_path) else 'https://cdn.pixabay.com/photo/2017/08/06/22/01/wedding-2595862_1280.jpg'
if not os.path.exists(image_path):
    logger.warning(f"Image {image_path} not found. Using fallback image.")

# Load seating data from CSV file
def load_seating_data():
    with lock:
        global seating_map
        seating_map.clear()
        try:
            with open('seating.txt', 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row['Name'].strip().lower()
                    title_match = re.match(r'^(mr\.|mrs\.|ms\.|dr\.|prof\.|major|brig\.)', name, re.IGNORECASE)
                    title = title_match.group(1).title() if title_match else ''
                    seating_map[name] = {
                        'table': row['Table No'],
                        'attendance': int(row['Attendance']),
                        'title': title
                    }
        except FileNotFoundError:
            logger.error("seating.txt not found.")
            seating_map['error'] = {'table': 'N/A', 'attendance': 0, 'title': ''}

load_seating_data()

# HTML Templates
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aruni & Harshamal's Wedding</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <link rel="icon" href="https://cdn.pixabay.com/photo/2016/02/14/09/48/heart-1199242_1280.png">
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <style>
        body {
            background-image: url('{{ background_image }}');
            background-size: contain;
            background-attachment: fixed;
            background-position: center;
        }
        .frosted-glass {
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(12px);
            border-radius: 20px;
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.3);
        }
        .btn-primary {
            background: linear-gradient(to right, #f472b6, #f9a8d4);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .btn-primary:hover {
            transform: scale(1.05);
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
        }
        input:focus {
            box-shadow: 0 0 0 3px rgba(244, 114, 182, 0.3);
        }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4 bg-gray-900 bg-opacity-50">
    <div class="frosted-glass p-8 max-w-lg w-full">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-6 font-serif">Aruni & Harshamal's Wedding</h1>
        <p class="text-center text-gray-600 mb-6">Welcome to our special day! Find your seat below.</p>
        {% if error %}
        <div class="mb-4 p-4 bg-red-100 text-red-800 rounded-lg text-center">
            {{ error }}
        </div>
        {% endif %}
        <form method="get" class="space-y-4">
            <div class="relative">
                <input type="text" id="guest" name="guest" placeholder="Enter your name" class="w-full p-4 border rounded-lg focus:outline-none focus:ring-2 focus:ring-pink-300 text-gray-800" required autocomplete="off">
                <div id="autocomplete-list" class="absolute w-full max-h-40 overflow-y-auto bg-white border rounded-lg shadow-lg mt-1 hidden z-10"></div>
            </div>
            <button class="w-full btn-primary text-white p-4 rounded-lg font-semibold">Find My Seat</button>
        </form>
        {% if result %}
        <div class="mt-6 p-4 bg-white rounded-lg shadow text-center text-gray-800">
            <p>{{ result }}</p>
        </div>
        {% endif %}
    </div>
    <script>
        $(document).ready(function() {
            $('#guest').on('input', function() {
                let query = $(this).val().toLowerCase();
                if (query.length < 2) {
                    $('#autocomplete-list').addClass('hidden').empty();
                    return;
                }
                $.get('/autocomplete', { guest: query }, function(data) {
                    $('#autocomplete-list').removeClass('hidden').empty();
                    if (data.length) {
                        data.forEach(name => {
                            $('#autocomplete-list').append(`<div class="p-2 hover:bg-gray-100 cursor-pointer">${name}</div>`);
                        });
                    } else {
                        $('#autocomplete-list').addClass('hidden');
                    }
                });
                $('#autocomplete-list').on('click', 'div', function() {
                    $('#guest').val($(this).text());
                    $('#autocomplete-list').addClass('hidden');
                });
            });
        });
    </script>
</body>
</html>
"""

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin - Seating Overview</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.datatables.net/1.11.5/js/jquery.dataTables.min.js"></script>
    <link href="https://cdn.datatables.net/1.11.5/css/jquery.dataTables.min.css" rel="stylesheet">
    <style>
        .table-container { max-height: 500px; overflow-y: auto; }
        .update-indicator { display: none; color: green; }
        .sidebar { background: linear-gradient(to bottom, #f472b6, #f9a8d4); }
        .card { background: rgba(255, 255, 255, 0.95); border-radius: 10px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); }
    </style>
</head>
<body class="bg-gray-100">
    <div class="flex min-h-screen">
        <!-- Sidebar -->
        <div class="sidebar w-64 p-6 text-white">
            <h2 class="text-2xl font-bold mb-6">Admin Panel</h2>
            <ul>
                <li><a href="/admin" class="block py-2 px-4 hover:bg-pink-600 rounded">Dashboard</a></li>
                <li><a href="/table_layout" class="block py-2 px-4 hover:bg-pink-600 rounded">Table Layout</a></li>
                <li><a href="/" class="block py-2 px-4 hover:bg-pink-600 rounded">Home</a></li>
                <li><a href="/export_csv" class="block py-2 px-4 hover:bg-pink-600 rounded">Export Data</a></li>
            </ul>
        </div>
        <!-- Main Content -->
        <div class="flex-1 p-6">
            <div class="max-w-7xl mx-auto">
                <h1 class="text-3xl font-bold mb-6 text-gray-800">Seating & Attendance Dashboard</h1>
                {% if error %}
                <div class="mb-4 p-4 bg-red-100 text-red-800 rounded-lg">
                    {{ error }}
                </div>
                {% endif %}
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
                    <div class="card p-6">
                        <h3 class="text-lg font-semibold mb-2">Total Guests</h3>
                        <p class="text-3xl font-bold text-gray-800" id="total-guests">{{ total_guests }}</p>
                    </div>
                    <div class="card p-6">
                        <h3 class="text-lg font-semibold mb-2">Attended</h3>
                        <p class="text-3xl font-bold text-gray-800" id="attended-guests">{{ attended_guests }}</p>
                    </div>
                    <div class="card p-6">
                        <h3 class="text-lg font-semibold mb-2">Attendance %</h3>
                        <p class="text-3xl font-bold text-gray-800" id="attendance-percentage">{{ attendance_percentage }}%</p>
                        <p class="update-indicator text-sm italic" id="update-indicator">Updated just now</p>
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                    <div class="card p-6">
                        <h2 class="text-xl font-semibold mb-4">Table-Wise Attendance</h2>
                        <canvas id="tableChart" height="200"></canvas>
                    </div>
                    <div class="card p-6">
                        <h2 class="text-xl font-semibold mb-4">Overall Attendance</h2>
                        <canvas id="pieChart" height="200"></canvas>
                    </div>
                </div>
                <div class="card p-6 mb-8">
                    <h2 class="text-xl font-semibold mb-4">Attendance Trend (Mock)</h2>
                    <canvas id="trendChart" height="200"></canvas>
                </div>
                <div class="card p-6">
                    <h2 class="text-xl font-semibold mb-4">Guest List</h2>
                    <table class="w-full border-collapse" id="guest-table">
                        <thead class="bg-gray-200 sticky top-0">
                            <tr>
                                <th class="p-2 border">Title</th>
                                <th class="p-2 border">Name</th>
                                <th class="p-2 border">Table No.</th>
                                <th class="p-2 border">Attendance</th>
                                <th class="p-2 border">Action</th>
                            </tr>
                        </thead>
                        <tbody id="guest-table-body">
                            {% for name, info in data.items() %}
                            <tr data-name="{{ name }}">
                                <td class="p-2 border">{{ info.title }}</td>
                                <td class="p-2 border">{{ name.title() }}</td>
                                <td class="p-2 border">{{ info.table }}</td>
                                <td class="p-2 border attendance-status">{{ 'Present' if info.attendance else 'Absent' }}</td>
                                <td class="p-2 border">
                                    <button class="toggle-attendance bg-blue-500 text-white px-2 py-1 rounded hover:bg-blue-600" data-name="{{ name }}">Toggle</button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    <script>
        let tableChart = null;
        let pieChart = null;
        let trendChart = null;

        $(document).ready(function() {
            // Initialize DataTable
            let table = $('#guest-table').DataTable({
                paging: true,
                searching: true,
                ordering: true,
                info: true,
                lengthChange: false,
                pageLength: 10
            });

            // Toggle attendance
            $(document).on('click', '.toggle-attendance', function() {
                let name = $(this).data('name');
                $.post('/toggle_attendance', { name: name }, function(data) {
                    let row = $(`tr[data-name="${name}"]`);
                    row.find('.attendance-status').text(data.attendance ? 'Present' : 'Absent');
                    table.draw();
                });
            });

            // Real-time updates with SSE
            let source = new EventSource('/admin_stream');
            source.onmessage = function(event) {
                let data = JSON.parse(event.data);
                updateDashboard(data);
                $('#update-indicator').show().fadeOut(2000);
            };

            function updateDashboard(data) {
                $('#total-guests').text(data.total_guests);
                $('#attended-guests').text(data.attended_guests);
                $('#attendance-percentage').text(data.attendance_percentage);

                // Update table
                table.clear();
                data.guests.forEach(guest => {
                    table.row.add([
                        guest.title,
                        guest.name,
                        guest.table,
                        guest.attendance ? 'Present' : 'Absent',
                        `<button class="toggle-attendance bg-blue-500 text-white px-2 py-1 rounded hover:bg-blue-600" data-name="${guest.name.toLowerCase()}">Toggle</button>`
                    ]);
                });
                table.draw();

                // Update table chart
                updateTableChart(data.table_stats);

                // Update pie chart
                updatePieChart(data.total_guests, data.attended_guests);

                // Update trend chart
                updateTrendChart(data.attended_guests);
            }

            function updateTableChart(tableStats) {
                const ctx = document.getElementById('tableChart').getContext('2d');
                const labels = tableStats.tables;
                const datasets = [
                    {
                        label: 'Total Guests',
                        data: tableStats.totals,
                        backgroundColor: 'rgba(54, 162, 235, 0.5)',
                        borderColor: 'rgba(54, 162, 235, 1)',
                        borderWidth: 1
                    },
                    {
                        label: 'Attended Guests',
                        data: tableStats.attended,
                        backgroundColor: 'rgba(75, 192, 192, 0.5)',
                        borderColor: 'rgba(75, 192, 192, 1)',
                        borderWidth: 1
                    }
                ];

                if (tableChart) {
                    tableChart.data.labels = labels;
                    tableChart.data.datasets = datasets;
                    tableChart.update();
                } else {
                    tableChart = new Chart(ctx, {
                        type: 'bar',
                        data: { labels, datasets },
                        options: {
                            scales: { y: { beginAtZero: true } },
                            responsive: true
                        }
                    });
                }
            }

            function updatePieChart(total, attended) {
                const ctx = document.getElementById('pieChart').getContext('2d');
                const data = {
                    labels: ['Present', 'Absent'],
                    datasets: [{
                        data: [attended, total - attended],
                        backgroundColor: ['rgba(75, 192, 192, 0.5)', 'rgba(255, 99, 132, 0.5)'],
                        borderColor: ['rgba(75, 192, 192, 1)', 'rgba(255, 99, 132, 1)'],
                        borderWidth: 1
                    }]
                };

                if (pieChart) {
                    pieChart.data = data;
                    pieChart.update();
                } else {
                    pieChart = new Chart(ctx, {
                        type: 'pie',
                        data: data,
                        options: { responsive: true }
                    });
                }
            }

            function updateTrendChart(attended) {
                const ctx = document.getElementById('trendChart').getContext('2d');
                const labels = ['1h ago', '45m ago', '30m ago', '15m ago', 'Now'];
                const data = {
                    labels: labels,
                    datasets: [{
                        label: 'Attended Guests (Mock)',
                        data: [50, 60, 70, 80, attended],
                        fill: false,
                        borderColor: 'rgba(75, 192, 192, 1)',
                        tension: 0.1
                    }]
                };

                if (trendChart) {
                    trendChart.data = data;
                    trendChart.update();
                } else {
                    trendChart = new Chart(ctx, {
                        type: 'line',
                        data: data,
                        options: {
                            scales: { y: { beginAtZero: true } },
                            responsive: true
                        }
                    });
                }
            }
        });
    </script>
</body>
</html>
"""

TABLE_LAYOUT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Table Layout</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <style>
        .table-circle {
            width: 100px;
            height: 100px;
            border-radius: 50%;
            background: linear-gradient(to right, #f472b6, #f9a8d4);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            margin: 10px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
            font-size: 0.9rem;
            text-align: center;
            position: relative;
        }
        .attendance-indicator {
            position: absolute;
            top: -5px;
            right: -5px;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            border: 2px solid white;
        }
    </style>
</head>
<body class="bg-gray-100 p-6">
    <div class="max-w-4xl mx-auto bg-white p-6 rounded-lg shadow">
        <h1 class="text-2xl font-bold mb-4 text-gray-800">Table Layout Preview</h1>
        <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {% for table, guests in table_layout.items() %}
            <div class="p-4 bg-gray-50 rounded-lg shadow">
                <h2 class="text-lg font-semibold mb-2 text-gray-800">Table {{ table }}</h2>
                <div class="flex flex-wrap">
                    {% for guest, attendance in guests %}
                    <div class="table-circle">
                        {{ guest.title() }}
                        <div class="attendance-indicator" style="background-color: {{ '#34d399' if attendance else '#ef4444' }}"></div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    guest = request.args.get('guest', '').strip().lower()
    result = None
    error = None
    if 'error' in seating_map:
        error = "Error: seating.txt not found. Please contact the administrator."
    elif guest:
        with lock:
            if guest in seating_map:
                seating_map[guest]['attendance'] = 1  # Mark as present
                # Update the seating.txt file
                with open('seating.txt', 'w', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Name', 'Table No', 'Attendance'])
                    for n, info in seating_map.items():
                        if n != 'error':
                            writer.writerow([n.title(), info['table'], info['attendance']])
                result = f"{guest.title()}, your seat is at Table {seating_map[guest]['table']}. Your attendance has been recorded."
            else:
                result = "Name not found. Please check spelling or ask a staff member."
    return render_template_string(HOME_TEMPLATE, result=result, error=error, background_image=background_image)

def get_admin_data():
    with lock:
        if 'error' in seating_map:
            return {
                'error': 'seating.txt not found.',
                'total_guests': 0,
                'attended_guests': 0,
                'attendance_percentage': 0,
                'guests': [],
                'table_stats': {'tables': [], 'totals': [], 'attended': []}
            }

        total_guests = len(seating_map)
        attended_guests = sum(1 for info in seating_map.values() if info['attendance'])
        attendance_percentage = round((attended_guests / total_guests * 100), 1) if total_guests > 0 else 0
        guests = [{'name': name.title(), 'table': info['table'], 'attendance': info['attendance'], 'title': info['title']} 
                  for name, info in seating_map.items() if name != 'error']

        # Compute table stats
        table_data = {}
        for name, info in seating_map.items():
            if name != 'error':
                table = info['table']
                if table not in table_data:
                    table_data[table] = {'total': 0, 'attended': 0}
                table_data[table]['total'] += 1
                if info['attendance']:
                    table_data[table]['attended'] += 1

        tables = sorted(table_data.keys(), key=lambda x: int(x) if x.isdigit() else x)
        totals = [table_data[table]['total'] for table in tables]
        attended = [table_data[table]['attended'] for table in tables]

        table_stats = {
            'tables': tables,
            'totals': totals,
            'attended': attended
        }

        return {
            'total_guests': total_guests,
            'attended_guests': attended_guests,
            'attendance_percentage': attendance_percentage,
            'guests': guests,
            'table_stats': table_stats
        }

@app.route('/admin')
def admin():
    data = get_admin_data()
    return render_template_string(ADMIN_TEMPLATE, data=seating_map, **data)

@app.route('/admin_data')
def admin_data():
    data = get_admin_data()
    return jsonify(data)

@app.route('/admin_stream')
def admin_stream():
    def stream():
        global last_data_hash
        while True:
            data = get_admin_data()
            data_str = json.dumps(data)
            data_hash = hashlib.md5(data_str.encode()).hexdigest()
            if data_hash != last_data_hash:
                last_data_hash = data_hash
                yield f"data: {data_str}\n\n"
            time.sleep(2)  # Check every 2 seconds
    return Response(stream(), mimetype='text/event-stream')

@app.route('/table_layout')
def table_layout():
    with lock:
        table_layout = {}
        for name, info in seating_map.items():
            if name != 'error':
                table = info['table']
                if table not in table_layout:
                    table_layout[table] = []
                table_layout[table].append((name, info['attendance']))
    return render_template_string(TABLE_LAYOUT_TEMPLATE, table_layout=table_layout)

@app.route('/autocomplete')
def autocomplete():
    query = request.args.get('guest', '').strip().lower()
    with lock:
        suggestions = [name.title() for name in seating_map.keys() if query in name and name != 'error'][:5]
    return jsonify(suggestions)

@app.route('/toggle_attendance', methods=['POST'])
def toggle_attendance():
    name = request.form.get('name').strip().lower()
    with lock:
        if name in seating_map and name != 'error':
            seating_map[name]['attendance'] = 1 if not seating_map[name]['attendance'] else 0
            # Update the seating.txt file
            with open('seating.txt', 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Name', 'Table No', 'Attendance'])
                for n, info in seating_map.items():
                    if n != 'error':
                        writer.writerow([n.title(), info['table'], info['attendance']])
            return jsonify({'attendance': seating_map[name]['attendance']})
    return jsonify({'error': 'Name not found'}), 404

@app.route('/export_csv')
def export_csv():
    with lock:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['Name', 'Title', 'Table No', 'Attendance'])
        for name, info in seating_map.items():
            if name != 'error':
                writer.writerow([name.title(), info['title'], info['table'], 'Present' if info['attendance'] else 'Absent'])
        output.seek(0)
    return send_file(
        output,
        mimetype='text/csv',
        as_attachment=True,
        download_name='seating_data.csv'
    )

if __name__ == '__main__':
    app.run(debug=True)