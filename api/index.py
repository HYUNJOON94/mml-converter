from http.server import BaseHTTPRequestHandler
import json
import mido
import io
from urllib.parse import parse_qs

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

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # Content-Length 헤더에서 데이터 크기 가져오기
            content_length = int(self.headers['Content-Length'])
            
            # 요청 본문 읽기
            post_data = self.rfile.read(content_length)
            
            # multipart/form-data 처리
            if 'multipart/form-data' in self.headers.get('Content-Type', ''):
                # 파일 데이터 추출 (간단한 구현)
                # 실제로는 더 복잡한 multipart 파싱이 필요할 수 있음
                file_start = post_data.find(b'\r\n\r\n') + 4
                file_end = post_data.rfind(b'\r\n-')
                midi_data = post_data[file_start:file_end]
                
                # MIDI to MML 변환
                result = midi_to_mml(midi_data)
                
                # 응답 전송
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            else:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': '잘못된 요청 형식입니다.'}).encode())
                
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers() 