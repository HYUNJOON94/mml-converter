from http.server import BaseHTTPRequestHandler
import json
import mido
import io
import math
import os
import cgi
import re
from urllib.parse import parse_qs

def get_note_length(ticks, ticks_per_beat):
    """MIDI 틱을 MML 음표 길이로 변환 (더 정확한 변환)"""
    # 음표 길이 비율 = 4분음표 대비 상대적 길이
    relative_length = ticks / ticks_per_beat
    
    # 마비노기 MML에서 가능한 음표 길이 (1, 2, 4, 8, 16, 32, 64)
    # 점음표도 지원 (2., 4., 8., 16.)
    
    # 가장 가까운 음표 길이 찾기
    standard_lengths = {
        '1': 4.0,     # 온음표(1분음표)는 4분음표의 4배
        '2': 2.0,     # 2분음표는 4분음표의 2배
        '4': 1.0,     # 4분음표
        '8': 0.5,     # 8분음표는 4분음표의 1/2
        '16': 0.25,   # 16분음표는 4분음표의 1/4
        '32': 0.125,  # 32분음표는 4분음표의 1/8
        '2.': 3.0,    # 점2분음표 (2분음표 + 4분음표)
        '4.': 1.5,    # 점4분음표 (4분음표 + 8분음표)
        '8.': 0.75,   # 점8분음표 (8분음표 + 16분음표)
        '16.': 0.375  # 점16분음표 (16분음표 + 32분음표)
    }
    
    # 가장 가까운 음표 길이 찾기
    closest_length = min(standard_lengths.items(), key=lambda x: abs(x[1] - relative_length))
    return closest_length[0]

# 마이크로초/박을 BPM으로 변환하는 함수 추가
def tempo_to_bpm(tempo_value):
    """마이크로초/박을 BPM으로 변환"""
    return round(60000000 / tempo_value)

def process_track(track, ticks_per_beat, ppq, tempo_events, is_harmony=False):
    """단일 트랙을 MML로 변환 (샘플 형식에 맞게 조정)"""
    mml = []
    current_octave = 4  # 기본 옥타브
    current_time = 0
    current_length = '8'  # 기본 음표 길이
    notes_on = {}  # 현재 켜져있는 노트를 추적 {note: start_time}
    active_notes = []  # 현재 활성화된 노트들
    events = []  # 모든 노트 이벤트를 저장 (시간순 정렬 위함)
    chord_times = {}  # 화음 시작 시간 {time: [notes]}
    last_length_change = ''  # 마지막 길이 변경 추적
    last_note_info = {'note': None, 'time': 0}  # 마지막 노트 정보
    processed_notes = set()  # 이미 처리된 노트 추적
    previous_note = None  # 이전 노트 추적
    
    # 이벤트 수집 및 시간 정렬
    for msg in track:
        current_time += msg.time
        
        if msg.type == 'note_on' and msg.velocity > 0:
            events.append({
                'type': 'note_on',
                'time': current_time,
                'note': msg.note,
                'velocity': msg.velocity
            })
            
            # 화음 감지를 위해 시간별 노트 그룹화
            time_key = round(current_time * 100) / 100  # 소수점 2자리까지 반올림하여 근접 이벤트 그룹화
            if time_key not in chord_times:
                chord_times[time_key] = []
            chord_times[time_key].append(msg.note)
            
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            events.append({
                'type': 'note_off',
                'time': current_time,
                'note': msg.note
            })
    
    # 이벤트가 없으면 빈 문자열 반환
    if not events:
        return ""
        
    # 이벤트를 시간순으로 정렬
    events.sort(key=lambda x: x['time'])
    
    # 화음 추출 - 정해진 시간 간격(0.05초) 내에 시작하는 노트를 화음으로 그룹화
    chord_groups = []
    current_chord = []
    last_time = -1
    
    for time_key in sorted(chord_times.keys()):
        if last_time < 0 or time_key - last_time > 0.05:  # 새로운 화음 그룹 시작
            if current_chord:
                chord_groups.append({'time': last_time, 'notes': sorted(current_chord)})
                current_chord = []
        
        current_chord.extend(chord_times[time_key])
        last_time = time_key
    
    # 마지막 화음 그룹 추가
    if current_chord:
        chord_groups.append({'time': last_time, 'notes': sorted(current_chord)})
    
    # 화음 이벤트를 시간순으로 정렬하고 병합
    chord_events = []
    for chord in chord_groups:
        # 단일 노트인 경우 일반 노트로 처리
        if len(chord['notes']) == 1:
            continue
            
        # 화음으로 처리
        chord_events.append({
            'type': 'chord',
            'time': chord['time'],
            'notes': chord['notes']
        })
    
    # 화음 이벤트와 단일 노트 이벤트 결합 및 정렬
    combined_events = events + chord_events
    combined_events.sort(key=lambda x: x['time'])
    
    # 템포 이벤트 병합
    all_events = []
    tempo_idx = 0
    
    for event in combined_events:
        # 현재 이벤트 시간보다 이전의 모든 템포 이벤트 추가
        while tempo_idx < len(tempo_events) and tempo_events[tempo_idx]['time'] <= event['time']:
            all_events.append(tempo_events[tempo_idx])
            tempo_idx += 1
        
        all_events.append(event)
    
    # 남은 템포 이벤트 추가
    while tempo_idx < len(tempo_events):
        all_events.append(tempo_events[tempo_idx])
        tempo_idx += 1
    
    # 이벤트를 시간순으로 다시 정렬
    all_events.sort(key=lambda x: x['time'])
    
    # MML로 변환
    note_names = ['C', 'C+', 'D', 'D+', 'E', 'F', 'F+', 'G', 'G+', 'A', 'A+', 'B']
    
    for i, event in enumerate(all_events):
        # 템포 이벤트 처리
        if event['type'] == 'tempo':
            mml.append(f"T{event['value']}")
            continue
            
        # 이전 이벤트와의 시간 차이 계산
        time_diff = 0
        if i > 0:
            time_diff = event['time'] - all_events[i-1]['time']
        
        # 화음 처리
        if event['type'] == 'chord' and not any(note in processed_notes for note in event['notes']):
            # 화음 내 모든 노트의 종료 시간 찾기
            chord_duration = float('inf')
            for note in event['notes']:
                for future_event in events:
                    if future_event['time'] > event['time'] and future_event['type'] == 'note_off' and future_event['note'] == note:
                        note_duration = future_event['time'] - event['time']
                        chord_duration = min(chord_duration, note_duration)
                        break
            
            if chord_duration == float('inf'):
                chord_duration = ticks_per_beat  # 기본값으로 1박자 설정
                
            # 화음 길이 설정
            note_length = get_note_length(chord_duration, ticks_per_beat)
            if note_length != current_length:
                mml.append(f'L{note_length}')
                current_length = note_length
                last_length_change = 'C'  # 화음에 의한 길이 변경
                
            # 화음 내 노트 처리하여 가장 낮은 음과 가장 높은 음을 찾음
            lowest_note = min(event['notes'])
            highest_note = max(event['notes'])
            
            # 최저음의 옥타브
            low_octave = (lowest_note // 12) - 1
            # 최고음의 옥타브
            high_octave = (highest_note // 12) - 1
            
            # 새로운 옥타브 설정 (화음은 일반적으로 최저음의 옥타브 사용)
            if low_octave != current_octave:
                if low_octave > current_octave:
                    mml.append('>' * (low_octave - current_octave))
                else:
                    mml.append('<' * (current_octave - low_octave))
                current_octave = low_octave
            
            # 마비노기에서는 한 명령어로 화음을 표현할 수 없어, 별도로 재생되는 파트로 분리 처리
            # 여기서는 최저음만 사용 (화음 파트는 별도 트랙으로 처리)
            chord_note_name = note_names[lowest_note % 12]
            mml.append(chord_note_name)
            
            # 처리된 노트 표시
            for note in event['notes']:
                processed_notes.add(note)
                
            # 마지막 노트 정보 업데이트
            last_note_info = {'note': event['notes'], 'time': event['time']}
            
        # 단일 노트 처리 (노트 온 이벤트)
        elif event['type'] == 'note_on' and event['note'] not in processed_notes:
            note = event['note']
            velocity = event['velocity']
            
            # 이미 처리된 노트 건너뛰기
            if note in processed_notes:
                continue
                
            # 새로운 옥타브 계산
            new_octave = (note // 12) - 1
            
            # 특별한 경우 C+를 D-, D+를 E-, F+를 G-, G+를 A-, A+를 B-로 표기하는 경우도 고려 (샘플에서 C- 등 사용)
            note_idx = note % 12
            note_name = note_names[note_idx]
            
            # 샘플에 맞게 일부 음표 표기법 수정 (C+와 C- 등)
            if note_name == 'C+' and 'C-' in ''.join(mml[-10:]):
                note_name = 'D-'  # 일관성 유지
            if note_name == 'D+' and 'D-' in ''.join(mml[-10:]):
                note_name = 'E-'  # 일관성 유지
            if note_name == 'F+' and 'F-' in ''.join(mml[-10:]):
                note_name = 'G-'  # 일관성 유지
            if note_name == 'G+' and 'G-' in ''.join(mml[-10:]):
                note_name = 'A-'  # 일관성 유지
            if note_name == 'A+' and 'A-' in ''.join(mml[-10:]):
                note_name = 'B-'  # 일관성 유지
                
            # 옥타브 변경이 필요한 경우 (샘플에서는 < >를 사용)
            if new_octave != current_octave:
                if new_octave > current_octave:
                    mml.append('>' * (new_octave - current_octave))
                else:
                    mml.append('<' * (current_octave - new_octave))
                current_octave = new_octave
            
            # 볼륨 설정 (MIDI 벨로시티를 MML 볼륨으로 변환)
            vol = max(1, min(15, math.ceil(velocity / 8)))  # 1-15 범위로 변환
            if vol != 13 and (len(mml) == 0 or not mml[-1].startswith('V')):
                mml.append(f'V{vol}')
            
            # 노트 시작 시간 저장
            notes_on[note] = event['time']
            active_notes.append(note)
            
            # 다음 노트까지의 길이 계산을 위해 노트 종료 이벤트 찾기
            note_duration = 0
            for future_event in events:
                if future_event['time'] > event['time'] and future_event['type'] == 'note_off' and future_event['note'] == note:
                    note_duration = future_event['time'] - event['time']
                    break
            
            # 음표 길이 설정 (샘플에서는 L 명령어 최소화 - 같은 길이 연속 사용 시 생략)
            if note_duration > 0:
                note_length = get_note_length(note_duration, ticks_per_beat)
                
                # 길이가 이전과 다르고, 바로 전에 길이 변경이 없었을 때만 L 붙임
                if note_length != current_length and last_length_change != 'N':
                    mml.append(f'L{note_length}')
                    current_length = note_length
                    last_length_change = 'N'
            
            # 타이 노트 (길게 이어지는 음표) 확인 - 샘플에서는 & 사용
            tie_note = False
            if previous_note == note and time_diff < ticks_per_beat * 0.125:  # 같은 음이 짧은 간격으로 연속될 때
                # 마지막 음표를 타이로 변경
                if len(mml) > 0 and mml[-1] == note_name:
                    mml.append(f"&{note_name}")
                    tie_note = True
            
            # MML 음표 추가 (타이 노트가 아닌 경우만)
            if not tie_note:
                mml.append(f"{note_name}")
                
            previous_note = note
            last_note_info = {'note': note, 'time': event['time']}
            processed_notes.add(note)
    
    # MML 문자열로 변환
    return ''.join(mml)

def midi_to_mml(midi_data):
    """MIDI 데이터를 멜로디/화음1/화음2로 나누어 MML로 변환"""
    # 바이트 데이터를 파일 객체로 변환
    midi_file = io.BytesIO(midi_data)
    mid = mido.MidiFile(file=midi_file)
    
    # MIDI 파일의 PPQ (펄스/분음표) 값
    ppq = mid.ticks_per_beat
    
    # 템포 이벤트 수집
    tempo_events = []
    current_time = 0
    default_tempo = 120  # 기본 템포 (BPM)
    
    # 모든 트랙에서 템포 이벤트 수집
    for track in mid.tracks:
        track_time = 0
        for msg in track:
            track_time += msg.time
            if msg.type == 'set_tempo':
                tempo_events.append({
                    'type': 'tempo',
                    'time': track_time,
                    'value': tempo_to_bpm(msg.tempo)
                })
    
    # 템포 이벤트 시간 기준 정렬
    tempo_events.sort(key=lambda x: x['time'])
    
    # 템포 이벤트가 없으면 기본 템포 추가
    if not tempo_events:
        tempo_events.append({
            'type': 'tempo',
            'time': 0,
            'value': default_tempo
        })
    
    # 첫 번째 템포 값 가져오기
    initial_tempo = tempo_events[0]['value']
    
    # 트랙별 정보 수집
    tracks_info = []
    for i, track in enumerate(mid.tracks):
        note_count = sum(1 for msg in track if msg.type == 'note_on' and msg.velocity > 0)
        if note_count > 0:
            tracks_info.append({
                'index': i,
                'note_count': note_count,
                'track': track
            })
    
    # 노트 수에 따라 트랙 정렬 (가장 많은 노트가 있는 트랙이 멜로디일 가능성 높음)
    tracks_info.sort(key=lambda x: x['note_count'], reverse=True)
    
    # 트랙 처리
    tracks_mml = []
    
    # 멜로디 트랙 (첫 번째 트랙)
    if tracks_info:
        melody_track = tracks_info[0]['track']
        melody_mml = process_track(melody_track, mid.ticks_per_beat, ppq, tempo_events, is_harmony=False)
        tracks_mml.append(melody_mml)
    else:
        tracks_mml.append("")
        
    # 화음 트랙들 (두 번째와 세 번째 트랙)
    for i in range(1, min(3, len(tracks_info))):
        harmony_track = tracks_info[i]['track']
        harmony_mml = process_track(harmony_track, mid.ticks_per_beat, ppq, tempo_events, is_harmony=True)
        tracks_mml.append(harmony_mml)
    
    # 최대 3개 트랙까지만 사용
    while len(tracks_mml) < 3:
        tracks_mml.append("")
    
    # 각 트랙에 템포 정보 추가 및 1200자 제한 적용
    # 템포 값은 이미 트랙 내부에 포함되어 있으므로 추가 템포 선언 삭제
    result = {
        "melody": tracks_mml[0][:1200],
        "harmony1": tracks_mml[1][:1200],
        "harmony2": tracks_mml[2][:1200]
    }
    
    return result

def parse_multipart_form_data(content_type, body):
    """멀티파트 폼 데이터 파싱"""
    boundary = content_type.split("boundary=")[1].encode()
    parts = body.split(b"--" + boundary)
    
    for part in parts:
        if b'filename=' in part and b'Content-Type: audio/midi' in part or b'Content-Type: audio/mid' in part:
            # 파일 헤더와 내용 구분
            headers_end = part.find(b"\r\n\r\n")
            if headers_end > 0:
                content = part[headers_end + 4:]
                return content
    
    return None

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        """CORS preflight 요청 처리"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()
    
    def do_POST(self):
        """MIDI 파일 업로드 및 MML 변환 처리"""
        try:
            # CORS 헤더 추가
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            # 요청 경로 확인
            if self.path != '/api/convert':
                self.wfile.write(json.dumps({"error": "잘못된 엔드포인트입니다"}).encode())
                return
            
            # Content-Type 헤더 확인
            content_type = self.headers.get('Content-Type', '')
            
            if 'multipart/form-data' in content_type:
                # 본문 길이 확인
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length == 0:
                    self.wfile.write(json.dumps({"error": "파일이 없습니다"}).encode())
                    return
                
                # 요청 본문 읽기
                body = self.rfile.read(content_length)
                
                # 멀티파트 폼 데이터 파싱
                midi_data = parse_multipart_form_data(content_type, body)
                
                if not midi_data:
                    self.wfile.write(json.dumps({"error": "MIDI 파일을 찾을 수 없습니다"}).encode())
                    return
                
                # MIDI를 MML로 변환
                result = midi_to_mml(midi_data)
                
                # 결과 반환
                self.wfile.write(json.dumps(result).encode())
            else:
                self.wfile.write(json.dumps({"error": "지원되지 않는 Content-Type입니다"}).encode())
        
        except Exception as e:
            # 오류 발생 시 처리
            self.wfile.write(json.dumps({"error": f"변환 중 오류가 발생했습니다: {str(e)}"}).encode()) 