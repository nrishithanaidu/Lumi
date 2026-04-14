"""
server.py — Lumi AI Document Intelligence
Flask server that serves the HTML frontend and proxies all API calls.
Run: python server.py
"""

import os
import sys
import json
import uuid
import time
import threading
import subprocess
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# Add lumi project path so we can import services
LUMI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lumi_project')
if os.path.exists(LUMI_PATH):
    sys.path.insert(0, LUMI_PATH)


def _emoji_to_text(emoji_str):
    """Replace common emojis with ASCII equivalents for Windows cp1252 safety."""
    replacements = {
        '✅': '[OK]', '❌': '[ERR]', '⚠️': '[WARN]', 'ℹ️': '[INFO]',
        '⚠': '[WARN]', '✓': '[OK]', '✗': '[ERR]',
    }
    for emoji, text in replacements.items():
        if emoji in emoji_str:
            return text
    return ''  # drop unknown emojis

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# ── In-memory job store (replace with DynamoDB in production) ─────────────────
JOBS = {}  # job_id -> dict
SETUP_LOG = []
SETUP_RUNNING = False
SETUP_DONE = False

CLEANUP_LOG = []
CLEANUP_RUNNING = False
CLEANUP_DONE = False

# ── Utility ───────────────────────────────────────────────────────────────────
def gen_job_id():
    return uuid.uuid4().hex[:8].upper()

def now():
    return datetime.utcnow().isoformat() + 'Z'

# ── Static / Frontend ─────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

# ── Setup endpoint (runs setup_resources.py via subprocess) ──────────────────
@app.route('/api/setup/start', methods=['POST'])
def setup_start():
    global SETUP_RUNNING, SETUP_DONE, SETUP_LOG
    if SETUP_RUNNING:
        return jsonify({'error': 'Setup already running'}), 409

    SETUP_LOG = []
    SETUP_RUNNING = True
    SETUP_DONE = False

    def run_setup():
        global SETUP_RUNNING, SETUP_DONE
        try:
            setup_script = os.path.join(LUMI_PATH, 'scripts', 'setup_resources.py')
            if not os.path.exists(setup_script):
                SETUP_LOG.append({'type': 'error', 'msg': f'Setup script not found at: {setup_script}'})
                SETUP_RUNNING = False
                return

            proc = subprocess.Popen(
                [sys.executable, setup_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=LUMI_PATH,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
            )

            for line in proc.stdout:
                clean = line.rstrip()
                if clean:
                    # Strip ANSI escape codes
                    import re
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', clean)
                    # Detect type BEFORE stripping emojis
                    msg_type = 'success' if any(x in clean for x in ['✅', 'OK', 'ok', 'Done', 'created', 'Connected']) else \
                               'error'   if any(x in clean for x in ['❌', 'Error', 'error', 'Failed', 'failed']) else \
                               'warning' if any(x in clean for x in ['⚠️', 'Warning', 'warn', 'Skipped']) else \
                               'info'    if any(x in clean for x in ['ℹ️', 'INFO', 'info', 'Running', 'Starting', 'Step']) else 'log'
                    # Strip emojis for Windows cp1252 safety
                    clean = re.sub(r'[^\x00-\x7F\u00A0-\u00FF]+', lambda m: _emoji_to_text(m.group()), clean)
                    SETUP_LOG.append({'type': msg_type, 'msg': clean})

            proc.wait()
            if proc.returncode == 0:
                SETUP_DONE = True
                SETUP_LOG.append({'type': 'success', 'msg': '✅ Setup completed successfully!'})
            else:
                SETUP_LOG.append({'type': 'error', 'msg': f'❌ Setup exited with code {proc.returncode}'})
        except Exception as e:
            SETUP_LOG.append({'type': 'error', 'msg': f'❌ Setup failed: {str(e)}'})
        finally:
            SETUP_RUNNING = False

    t = threading.Thread(target=run_setup, daemon=True)
    t.start()
    return jsonify({'status': 'started'})

@app.route('/api/setup/status')
def setup_status():
    return jsonify({
        'running': SETUP_RUNNING,
        'done':    SETUP_DONE,
        'logs':    SETUP_LOG[-200:],  # last 200 lines
    })

# ── Upload endpoint ───────────────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file     = request.files['file']
    doc_type = request.form.get('doc_type', 'other')
    run_ai   = request.form.get('run_ai', 'false').lower() == 'true'
    run_rag  = request.form.get('run_rag', 'false').lower() == 'true'

    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    job_id    = gen_job_id()
    filename  = file.filename
    timestamp = now()

    # Save to temp uploads dir
    uploads_dir = os.path.join(LUMI_PATH, 'uploads') if os.path.exists(LUMI_PATH) else '/tmp/lumi_uploads'
    os.makedirs(uploads_dir, exist_ok=True)
    local_path = os.path.join(uploads_dir, f'{job_id}_{filename}')
    file.save(local_path)

    JOBS[job_id] = {
        'job_id':    job_id,
        'filename':  filename,
        'doc_type':  doc_type,
        'status':    'uploaded',
        'timestamp': timestamp,
        'file_size': f'{os.path.getsize(local_path) / 1024 / 1024:.2f} MB',
        'run_ai':    run_ai,
        'run_rag':   run_rag,
        'local_path': local_path,
    }

    # Run pipeline in background
    def run_pipeline():
        try:
            from pipeline.process_document import run_pipeline as _run
            result = _run(local_path, doc_type=doc_type, run_ai=run_ai, run_rag=run_rag)
            JOBS[job_id].update(result)
            JOBS[job_id]['status'] = result.get('status', 'textract_done')
        except ImportError:
            # Fallback simulation if lumi modules not available
            time.sleep(2)
            JOBS[job_id]['status'] = 'textract_done'
            JOBS[job_id]['extracted_text'] = f'[Simulated] Text extracted from {filename}'
            JOBS[job_id]['page_count'] = 1
            JOBS[job_id]['line_count'] = 42
            JOBS[job_id]['table_count'] = 1
            JOBS[job_id]['summary'] = f'This document ({filename}) was processed. Backend AWS services are needed for real extraction.'
            JOBS[job_id]['category'] = 'Other'
        except Exception as e:
            JOBS[job_id]['status'] = 'failed'
            JOBS[job_id]['error'] = str(e)

    t = threading.Thread(target=run_pipeline, daemon=True)
    t.start()

    return jsonify({
        'job_id':    job_id,
        'filename':  filename,
        'doc_type':  doc_type,
        'status':    'uploaded',
        'timestamp': timestamp,
        'message':   'File uploaded. Poll /api/status/{job_id} for updates.',
    })

# ── Status endpoint ───────────────────────────────────────────────────────────
@app.route('/api/status/<job_id>')
def get_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        # Try fetching from DynamoDB if available
        try:
            from services.dynamodb_service import get_record
            record = get_record(job_id)
            if record:
                return jsonify(record)
        except Exception:
            pass
        return jsonify({'error': f'Job {job_id} not found'}), 404

    return jsonify({
        'job_id':      job.get('job_id'),
        'status':      job.get('status', 'processing'),
        'filename':    job.get('filename'),
        'doc_type':    job.get('doc_type'),
        'page_count':  job.get('page_count') or job.get('pages'),
        'rag_indexed': str(job.get('rag_indexed', False)).lower(),
        'timestamp':   job.get('timestamp'),
        'file_size':   job.get('file_size'),
    })

# ── Results endpoint ──────────────────────────────────────────────────────────
@app.route('/api/results/<job_id>')
def get_results(job_id):
    job = JOBS.get(job_id)
    if not job:
        try:
            from services.dynamodb_service import get_record
            job = get_record(job_id)
            if not job:
                return jsonify({'error': f'Job {job_id} not found'}), 404
        except Exception:
            return jsonify({'error': f'Job {job_id} not found'}), 404

    entities = job.get('entities')
    if isinstance(entities, str):
        try:
            entities = json.loads(entities)
        except Exception:
            entities = None

    return jsonify({
        'job_id':          job.get('job_id'),
        'filename':        job.get('filename'),
        'doc_type':        job.get('doc_type'),
        'status':          job.get('status'),
        'page_count':      job.get('page_count') or job.get('pages'),
        'line_count':      job.get('line_count'),
        'table_count':     job.get('table_count') or len(job.get('tables', [])),
        'extracted_text':  str(job.get('extracted_text') or job.get('full_text', ''))[:2000],
        'summary':         job.get('summary'),
        'entities':        entities,
        'category':        job.get('category'),
        'rag_indexed':     str(job.get('rag_indexed', False)).lower(),
        'timestamp':       job.get('timestamp'),
        'avg_confidence':  job.get('avg_confidence'),
    })

# ── List jobs ─────────────────────────────────────────────────────────────────
@app.route('/api/jobs')
def list_jobs():
    jobs = []
    for job in JOBS.values():
        jobs.append({
            'job_id':    job.get('job_id'),
            'filename':  job.get('filename'),
            'doc_type':  job.get('doc_type'),
            'status':    job.get('status'),
            'timestamp': job.get('timestamp'),
            'file_size': job.get('file_size'),
        })
    # Try DynamoDB too
    try:
        from services.dynamodb_service import list_records
        db_records = list_records(limit=50)
        existing_ids = {j['job_id'] for j in jobs}
        for r in db_records:
            if r.get('job_id') not in existing_ids:
                jobs.append({
                    'job_id':    r.get('job_id'),
                    'filename':  r.get('filename'),
                    'doc_type':  r.get('doc_type'),
                    'status':    r.get('status'),
                    'timestamp': r.get('timestamp'),
                })
    except Exception:
        pass

    jobs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return jsonify({'jobs': jobs, 'total': len(jobs)})

# ── Q&A endpoint ──────────────────────────────────────────────────────────────
@app.route('/api/query', methods=['POST'])
def query_document():
    body = request.get_json() or {}
    job_id   = body.get('job_id', '').strip()
    question = body.get('question', '').strip()

    if not job_id:
        return jsonify({'error': 'job_id is required'}), 400
    if not question:
        return jsonify({'error': 'question is required'}), 400

    try:
        from services.rag_service import query, list_indexed_documents
        indexed = list_indexed_documents()
        if job_id not in indexed:
            return jsonify({'error': f'Document {job_id} is not indexed for Q&A. Process with RAG enabled.'}), 404
        result = query(job_id, question)
        return jsonify({
            'job_id':      job_id,
            'question':    question,
            'answer':      result['answer'],
            'chunks_used': len(result.get('retrieved_chunks', [])),
        })
    except ImportError:
        # Fallback: use Bedrock directly if available
        job = JOBS.get(job_id)
        if not job:
            return jsonify({'error': f'Job {job_id} not found'}), 404

        text = str(job.get('full_text') or job.get('extracted_text', ''))
        if not text:
            return jsonify({'error': 'No extracted text found for this document. Enable RAG during upload.'}), 400

        try:
            from services.bedrock_service import answer_question
            answer = answer_question(question, [text[:4000]])
            return jsonify({
                'job_id':      job_id,
                'question':    question,
                'answer':      answer,
                'chunks_used': 1,
            })
        except Exception as e:
            return jsonify({'error': f'Q&A failed: {str(e)}'}), 500

# ── AWS connection check ───────────────────────────────────────────────────────
@app.route('/api/health')
def health():
    services = {}
    env_vars = {
        'AWS_ACCESS_KEY_ID':     bool(os.getenv('AWS_ACCESS_KEY_ID')),
        'AWS_SECRET_ACCESS_KEY': bool(os.getenv('AWS_SECRET_ACCESS_KEY')),
        'AWS_REGION':            os.getenv('AWS_REGION', 'us-east-1'),
        'S3_RAW_BUCKET':         os.getenv('S3_RAW_BUCKET', 'not set'),
        'DYNAMODB_TABLE':        os.getenv('DYNAMODB_TABLE', 'not set'),
    }

    try:
        import boto3
        session = boto3.Session(
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1'),
        )
        session.client('s3').list_buckets()
        services['s3'] = 'connected'
    except Exception as e:
        services['s3'] = f'error: {str(e)[:60]}'

    try:
        import boto3
        session = boto3.Session(
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1'),
        )
        session.client('dynamodb').list_tables(Limit=1)
        services['dynamodb'] = 'connected'
    except Exception as e:
        services['dynamodb'] = f'error: {str(e)[:60]}'

    try:
        import boto3
        session = boto3.Session(
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1'),
        )
        session.client('bedrock-runtime')
        services['bedrock'] = 'client_ready'
    except Exception as e:
        services['bedrock'] = f'error: {str(e)[:60]}'

    return jsonify({
        'status':   'ok',
        'env':      env_vars,
        'services': services,
        'jobs_in_memory': len(JOBS),
        'setup_done': SETUP_DONE,
    })

# ── Config save endpoint ──────────────────────────────────────────────────────
@app.route('/api/config', methods=['POST'])
def save_config():
    body = request.get_json() or {}
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    lumi_env = os.path.join(LUMI_PATH, '.env') if os.path.exists(LUMI_PATH) else None

    lines = []
    for key, val in body.items():
        if val:
            lines.append(f'{key}={val}')
            os.environ[key] = str(val)

    content = '\n'.join(lines) + '\n'

    with open(env_path, 'w') as f:
        f.write(content)

    if lumi_env:
        with open(lumi_env, 'w') as f:
            f.write(content)

    return jsonify({'status': 'saved', 'message': 'Configuration saved. Reload to apply.'})

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify({
        'AWS_ACCESS_KEY_ID':     '***' if os.getenv('AWS_ACCESS_KEY_ID') else '',
        'AWS_SECRET_ACCESS_KEY': '***' if os.getenv('AWS_SECRET_ACCESS_KEY') else '',
        'AWS_REGION':            os.getenv('AWS_REGION', 'us-east-1'),
        'S3_RAW_BUCKET':         os.getenv('S3_RAW_BUCKET', ''),
        'S3_PROCESSED_BUCKET':   os.getenv('S3_PROCESSED_BUCKET', ''),
        'DYNAMODB_TABLE':        os.getenv('DYNAMODB_TABLE', ''),
    })


# ── Cleanup endpoint ──────────────────────────────────────────────────────────
@app.route('/api/cleanup/start', methods=['POST'])
def cleanup_start():
    global CLEANUP_RUNNING, CLEANUP_DONE, CLEANUP_LOG
    if CLEANUP_RUNNING:
        return jsonify({'error': 'Cleanup already running'}), 409

    body = request.get_json() or {}
    mode = body.get('mode', 'soft')  # soft | hard | local

    if mode not in ('soft', 'hard', 'local'):
        return jsonify({'error': 'mode must be soft, hard, or local'}), 400

    CLEANUP_LOG = []
    CLEANUP_RUNNING = True
    CLEANUP_DONE = False

    def run_cleanup():
        global CLEANUP_RUNNING, CLEANUP_DONE
        try:
            cleanup_script = os.path.join(LUMI_PATH, 'scripts', 'cleanup.py')
            if not os.path.exists(cleanup_script):
                CLEANUP_LOG.append({'type': 'error', 'msg': f'Cleanup script not found at: {cleanup_script}'})
                CLEANUP_RUNNING = False
                return

            # Pass --hard or --local flags; soft is the default (no flag)
            args = [sys.executable, cleanup_script]
            if mode == 'hard':
                args.append('--hard')
            elif mode == 'local':
                args.append('--local')

            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,   # no interactive prompts
                text=True,
                cwd=LUMI_PATH,
                env={**os.environ, 'LUMI_NO_CONFIRM': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
            )

            for line in proc.stdout:
                clean = line.rstrip()
                if clean:
                    import re
                    clean = re.sub(r'\x1b\[[0-9;]*m', '', clean)
                    msg_type = 'success' if any(x in clean for x in ['✅', 'OK', 'ok', 'Done', 'created', 'Connected', 'Deleted', 'Emptied', 'Cleared']) else \
                               'error'   if any(x in clean for x in ['❌', 'Error', 'error', 'Failed', 'failed']) else \
                               'warning' if any(x in clean for x in ['⚠️', 'Warning', 'warn', 'Skipped', 'Not found', 'cancelled']) else \
                               'info'    if any(x in clean for x in ['ℹ️', 'INFO', 'info', 'Running', 'Starting', 'Step', 'Deleting', 'Emptying']) else 'log'
                    clean = re.sub(r'[^\x00-\x7F\u00A0-\u00FF]+', lambda m: _emoji_to_text(m.group()), clean)
                    CLEANUP_LOG.append({'type': msg_type, 'msg': clean})

            proc.wait()
            if proc.returncode == 0:
                CLEANUP_DONE = True
                CLEANUP_LOG.append({'type': 'success', 'msg': f'✅ {mode.capitalize()} cleanup completed!'})
            else:
                CLEANUP_LOG.append({'type': 'error', 'msg': f'❌ Cleanup exited with code {proc.returncode}'})
        except Exception as e:
            CLEANUP_LOG.append({'type': 'error', 'msg': f'❌ Cleanup failed: {str(e)}'})
        finally:
            CLEANUP_RUNNING = False

    t = threading.Thread(target=run_cleanup, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'mode': mode})


@app.route('/api/cleanup/status')
def cleanup_status():
    return jsonify({
        'running': CLEANUP_RUNNING,
        'done':    CLEANUP_DONE,
        'logs':    CLEANUP_LOG[-200:],
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f'\n{"="*55}')
    print(f'  Lumi — AI Document Intelligence')
    print(f'  Server running at http://localhost:{port}')
    print(f'{"="*55}\n')
    app.run(host='0.0.0.0', port=port, debug=False)
