from flask import Flask, request, jsonify, send_file, render_template
import uuid
import datetime
import pandas as pd
from utils import *
import io
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor
import asyncio

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=10)

@app.route('/')
def Home():
    return render_template('index.html')

@app.route('/upload_csv', methods=['POST'])
async def upload_csv():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    try:
        df = pd.read_csv(file)

        if df.empty:
            return jsonify({"error": "No data in this uploaded CSV file"}), 400
        request_id = uuid.uuid4()

        loop = asyncio.get_running_loop()
        loop.run_in_executor(executor, process_csv, df, request_id)

        if not request_id:
            return jsonify({"error": "Failed to process CSV"}), 500

        return jsonify({"request_id": str(request_id)}), 200

    except Exception as e:
        return jsonify({"error": f"Error processing file: {str(e)}"}), 500

@app.route('/check_status/<uuid:request_id>', methods=['GET'])
async def check_request_status(request_id):
    status = check_status(request_id)
    return jsonify({"request_id": str(request_id), "status": status}), 200

@app.route('/download_csv/<uuid:request_id>', methods=['GET'])
def download_csv(request_id):
    try:
        data = get_data_db(request_id)

        if data.empty:
            return jsonify({"error": "No data found for the given request_id"}), 404

        output = io.BytesIO()
        data.to_csv(output, index=False, encoding='utf-8')
        output.seek(0)

        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name="product_urls.csv"
        )

    except Exception as e:
        print(f"Error generating CSV: {e}")
        return jsonify({"error": "Error generating CSV"}), 500


if __name__ == '__main__':
    app.run(debug=True)