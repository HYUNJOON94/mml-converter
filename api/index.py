from flask import Flask, render_template, request, jsonify
import mido
import io
from werkzeug.utils import secure_filename

app = Flask(__name__, template_folder='../templates')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

def get_note_length(ticks, ticks_per_beat):
    """MIDI 틱을 MML 음표 길이로 변환"""
    relative_length = ticks / ticks_per_beat
    if relative_length >= 2:  # 2분음표 이상
        return '2'
    elif relative_length >= 1:  # 4분음표
        return '4'
    elif relative_length >= 0.5:  # 8분음표
        return '8'
    else:  # 16분음표
        return '16'

def process_track(track, ticks_per_beat):
    """단일 트랙을 MML로 변환"""
    mml = []
    current_octave = 4  # 기본 옥타브
    current_time = 0
    current_length = '8'  # 기본 음표 길이
    notes_on = {}  # 현재 켜져있는 노트를 추적

    # 시작할 때 기본 설정 추가
    mml.append('V13')  # 기본 볼륨
    mml.append(f'L{current_length}')  # 기본 음표 길이

    for msg in track:
        current_time += msg.time
        
        if msg.type == 'note_on' and msg.velocity > 0:
            note = msg.note
            new_octave = (note // 12) - 1
            note_name = ['C', 'C+', 'D', 'D+', 'E', 'F', 'F+', 'G', 'G+', 'A', 'A+', 'B'][note % 12]
            
            # 옥타브 변경이 필요한 경우
            if new_octave != current_octave:
                if new_octave > current_octave:
                    mml.append('>' * (new_octave - current_octave))
                else:
                    mml.append('<' * (current_octave - new_octave))
                current_octave = new_octave
            
            # 음표 길이 계산
            note_length = get_note_length(msg.time, ticks_per_beat)
            if note_length != current_length:
                mml.append(f'L{note_length}')
                current_length = note_length
            
            notes_on[note] = current_time
            
            # MML 음표 추가 (공백 없이)
            mml.append(f"{note_name}")
            
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if msg.note in notes_on:
                # 쉼표 추가
                rest_ticks = current_time - notes_on[msg.note]
                if rest_ticks > 0:
                    rest_length = get_note_length(rest_ticks, ticks_per_beat)
                    if rest_length != current_length:
                        mml.append(f'L{rest_length}')
                        current_length = rest_length
                    mml.append('R')  # 쉼표 추가
                del notes_on[msg.note]
    
    return ''.join(mml)  # 공백 없이 모든 요소를 연결

def midi_to_mml(midi_data):
    """MIDI 데이터를 멜로디/화음1/화음2로 나누어 MML로 변환"""
    # 바이트 데이터를 파일 객체로 변환
    midi_file = io.BytesIO(midi_data)
    mid = mido.MidiFile(file=midi_file)
    tempo = 120  # 기본 템포
    
    # 템포 정보 찾기
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = round(mido.tempo2bpm(msg.tempo))  # 템포를 정수로 반올림
                break
        if tempo != 120:  # 템포를 찾았으면 루프 종료
            break
    
    # 트랙 처리
    tracks_mml = []
    for track in mid.tracks:
        if any(msg.type in ['note_on', 'note_off'] for msg in track):
            mml = process_track(track, mid.ticks_per_beat)
            if mml.strip():  # 빈 트랙이 아닌 경우만 추가
                tracks_mml.append(mml)
    
    # 최대 3개 트랙까지만 사용
    while len(tracks_mml) < 3:
        tracks_mml.append("")
    tracks_mml = tracks_mml[:3]
    
    # 각 트랙에 템포 정보 추가 및 1200자 제한 적용
    result = {
        "melody": f"T{tempo}R{tracks_mml[0]}"[:1200],  # 시작할 때 쉼표 추가
        "harmony1": f"T{tempo}R{tracks_mml[1]}"[:1200],
        "harmony2": f"T{tempo}R{tracks_mml[2]}"[:1200]
    }
    
    return result

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/api/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': '파일이 없습니다'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': '선택된 파일이 없습니다'}), 400
    
    if not file.filename.endswith('.mid'):
        return jsonify({'error': 'MIDI 파일만 업로드 가능합니다'}), 400
    
    try:
        # 파일 데이터를 직접 메모리에서 처리
        midi_data = file.read()
        result = midi_to_mml(midi_data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'변환 중 오류가 발생했습니다: {str(e)}'}), 500

# Vercel의 서버리스 함수를 위한 handler
app.debug = False 