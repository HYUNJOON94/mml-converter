from flask import Flask, render_template, request, jsonify, send_from_directory
import mido
import io
import math
from werkzeug.utils import secure_filename
import os
import re

app = Flask(__name__, template_folder='public', static_folder='public')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max-limit

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

def process_track(track, ticks_per_beat, ppq, is_harmony=False):
    """단일 트랙을 MML로 변환 (샘플 형식에 맞게 조정, 끊김 문제 해결)"""
    mml = []
    current_octave = 4  # 기본 옥타브
    current_time = 0
    current_length = '8'  # 기본 음표 길이
    notes_on = {}  # 현재 켜져있는 노트를 추적 {note: start_time}
    active_notes = []  # 현재 활성화된 노트들
    events = []  # 모든 노트 이벤트를 저장 (시간순 정렬 위함)
    chord_times = {}  # 화음 시작 시간 {time: [notes]}
    
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
    
    for time, notes in sorted(chord_times.items()):
        if last_time == -1 or time - last_time < 0.05 * ticks_per_beat:
            # 이전 노트와 가까운 시간에 있는 노트들은 같은 화음으로 그룹화
            current_chord.extend(notes)
        else:
            # 새로운 화음 시작
            if current_chord:
                chord_groups.append(current_chord)
            current_chord = notes.copy()
        last_time = time
    
    if current_chord:
        chord_groups.append(current_chord)
    
    # 화음 디버깅
    # print(f"Found {len(chord_groups)} chord groups")
    # for i, chord in enumerate(chord_groups[:10]):
    #     print(f"Chord {i}: {chord}")
    
    # 시작할 때 기본 설정 추가
    mml.append('V13')  # 기본 볼륨 (샘플과 일치)
    
    current_time = 0
    previous_time = 0
    previous_note = None
    last_length_change = None
    processed_notes = set()  # 이미 처리된 노트 추적
    
    # 마지막 음표 시간과 길이 (타이 노트 처리용)
    last_note_info = {'note': None, 'time': 0, 'length': 0, 'name': ''}
    
    # 기본 음표 길이 설정 (샘플처럼)
    mml.append(f'L{current_length}')
    
    # 각 이벤트 처리
    for event_idx, event in enumerate(events):
        # 이미 처리된 노트는 건너뛰기 (화음 처리 시 중복 방지)
        if is_harmony and event['type'] == 'note_on' and event['note'] in processed_notes:
            continue
            
        # 이전 이벤트와의 시간 차이 계산
        time_diff = event['time'] - previous_time
        
        # 짧은 쉼표는 건너뛰고, 실제로 필요한 쉼표만 추가 (끊김 방지)
        if time_diff > 0 and time_diff / ticks_per_beat >= 0.2:  # 최소 0.2박자 이상일 때만 쉼표 추가
            rest_length = get_note_length(time_diff, ticks_per_beat)
            
            # 길이가 이전과 다른 경우만 L 붙임 (샘플에서는 길이 변경 시에만 L 사용)
            if rest_length != current_length:
                mml.append(f'L{rest_length}')
                current_length = rest_length
                last_length_change = 'R'
            
            mml.append('R')
        
        if event['type'] == 'note_on':
            note = event['note']
            velocity = event['velocity']
            new_octave = (note // 12) - 1
            
            # 화음 처리 (is_harmony가 True인 경우)
            if is_harmony:
                # 현재 노트가 속한 화음 찾기
                current_chord = None
                for chord in chord_groups:
                    if note in chord:
                        current_chord = chord
                        break
                
                # 화음이 있고, 아직 처리되지 않은 노트가 있는 경우
                if current_chord and any(n not in processed_notes for n in current_chord):
                    # 화음의 가장 낮은 음과 가장 높은 음 찾기 (마비노기는 2음 화음만 지원)
                    unprocessed = [n for n in current_chord if n not in processed_notes]
                    if len(unprocessed) >= 2:
                        low_note = min(unprocessed)
                        high_note = max(unprocessed)
                        
                        # 두 음이 너무 멀리 떨어져 있으면 (옥타브 이상) 가까운 두 음 선택
                        if high_note - low_note > 12 and len(unprocessed) > 2:
                            # 간격이 가장 적절한 두 음 선택
                            sorted_notes = sorted(unprocessed)
                            min_interval = 12  # 초기값: 옥타브
                            selected_pair = (sorted_notes[0], sorted_notes[1])
                            
                            for i in range(len(sorted_notes) - 1):
                                interval = sorted_notes[i+1] - sorted_notes[i]
                                if 2 <= interval <= 7:  # 3도~5도 간격 선호
                                    selected_pair = (sorted_notes[i], sorted_notes[i+1])
                                    break
                                elif interval < min_interval:
                                    min_interval = interval
                                    selected_pair = (sorted_notes[i], sorted_notes[i+1])
                            
                            low_note, high_note = selected_pair
                        
                        # 선택된 두 음 처리
                        low_oct = (low_note // 12) - 1
                        high_oct = (high_note // 12) - 1
                        
                        note_names = ['C', 'C+', 'D', 'D+', 'E', 'F', 'F+', 'G', 'G+', 'A', 'A+', 'B']
                        low_name = note_names[low_note % 12]
                        high_name = note_names[high_note % 12]
                        
                        # 샘플에 맞게 일부 음표 표기법 수정
                        for name_var in ['C+', 'D+', 'F+', 'G+', 'A+']:
                            base = name_var[0]
                            if low_name == name_var and f'{base}-' in ''.join(mml[-10:]):
                                low_name = f'{base}-'
                            if high_name == name_var and f'{base}-' in ''.join(mml[-10:]):
                                high_name = f'{base}-'
                        
                        # 옥타브 변경이 필요한 경우
                        if low_oct != current_octave:
                            if low_oct > current_octave:
                                mml.append('>' * (low_oct - current_octave))
                            else:
                                mml.append('<' * (current_octave - low_oct))
                            current_octave = low_oct
                        
                        # 음표 길이 설정
                        # 노트 길이 계산
                        note_duration = 0
                        for future_event in events:
                            if future_event['time'] > event['time'] and future_event['type'] == 'note_off' and future_event['note'] == low_note:
                                note_duration = future_event['time'] - event['time']
                                break
                        
                        if note_duration > 0:
                            note_length = get_note_length(note_duration, ticks_per_beat)
                            if note_length != current_length:
                                mml.append(f'L{note_length}')
                                current_length = note_length
                                last_length_change = 'N'
                        
                        # 화음 추가 (2음 화음)
                        if high_oct == low_oct:
                            # 같은 옥타브 내의 화음
                            chord_name = f"{low_name}{high_name}"
                            mml.append(chord_name)
                            
                            # 마지막 음표 정보 업데이트
                            last_note_info = {
                                'note': [low_note, high_note], 
                                'time': event['time'], 
                                'length': note_duration,
                                'name': chord_name
                            }
                        elif high_oct == low_oct + 1 and low_note % 12 >= 9 and high_note % 12 <= 2:
                            # 옥타브가 바뀌지만 실제로는 가까운 음들 (예: B와 다음 옥타브의 C)
                            chord_name = f"{low_name}{high_name}"
                            mml.append(chord_name)
                            
                            # 마지막 음표 정보 업데이트
                            last_note_info = {
                                'note': [low_note, high_note], 
                                'time': event['time'], 
                                'length': note_duration,
                                'name': chord_name
                            }
                        else:
                            # 다른 옥타브의 화음은 순차적으로 처리
                            mml.append(f"{low_name}")
                            
                            # 높은 음의 옥타브로 변경
                            if high_oct > current_octave:
                                mml.append('>' * (high_oct - current_octave))
                            else:
                                mml.append('<' * (current_octave - high_oct))
                            current_octave = high_oct
                            
                            mml.append(f"{high_name}")
                            
                            # 다시 낮은 음의 옥타브로 변경
                            if low_oct > current_octave:
                                mml.append('>' * (low_oct - current_octave))
                            else:
                                mml.append('<' * (current_octave - low_oct))
                            current_octave = low_oct
                            
                            # 마지막 음표 정보 업데이트
                            last_note_info = {
                                'note': [low_note, high_note], 
                                'time': event['time'], 
                                'length': note_duration,
                                'name': f"{low_name}+{high_name}"
                            }
                        
                        # 처리된 노트 표시
                        processed_notes.add(low_note)
                        processed_notes.add(high_note)
                        
                        # 이벤트 처리 후 다음 이벤트로 넘어감
                        previous_time = event['time']
                        continue
            
            # 단일 음표 처리 (화음이 아니거나, 화음 처리 후 남은 노트)
            # 샘플과 같은 음표 표기법 사용
            note_names = ['C', 'C+', 'D', 'D+', 'E', 'F', 'F+', 'G', 'G+', 'A', 'A+', 'B']
            # 특별한 경우 C+를 D-, D+를 E-, F+를 G-, G+를 A-, A+를 B-로 표기하는 경우도 고려 (샘플에서 C- 등 사용)
            note_idx = note % 12
            note_name = note_names[note_idx]
            
            # 샘플에 맞게 일부 음표 표기법 수정 (C+와 C- 등)
            if note_name == 'C+' and 'C-' in ''.join(mml[-10:]):
                note_name = 'C-'  # 이전에 C-가 사용되었다면 일관성 유지
            if note_name == 'D+' and 'D-' in ''.join(mml[-10:]):
                note_name = 'D-'  # 이전에 D-가 사용되었다면 일관성 유지
            if note_name == 'F+' and 'F-' in ''.join(mml[-10:]):
                note_name = 'F-'  # 이전에 F-가 사용되었다면 일관성 유지
            if note_name == 'G+' and 'G-' in ''.join(mml[-10:]):
                note_name = 'G-'  # 이전에 G-가 사용되었다면 일관성 유지
            if note_name == 'A+' and 'A-' in ''.join(mml[-10:]):
                note_name = 'A-'  # 이전에 A-가 사용되었다면 일관성 유지
                
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
                if note_length != current_length:
                    mml.append(f'L{note_length}')
                    current_length = note_length
                    last_length_change = 'N'
            
            # 타이 노트 처리 - 쉼표 없이 같은 음이 반복될 때 타이로 연결
            tie_note = False
            
            # 같은 음표가 반복될 때 타이 노트로 처리
            if (isinstance(last_note_info['note'], int) and note == last_note_info['note']) or \
               (isinstance(last_note_info['note'], list) and note in last_note_info['note']):
                # 시간 간격이 매우 짧은 경우 또는 바로 이어지는 경우
                if time_diff < ticks_per_beat * 0.1:  # 0.1박자 이내면 타이 노트로 간주
                    # 앞선 음표와 합쳐서 &로 연결
                    if mml and not mml[-1].startswith('L') and not mml[-1].startswith('V'):
                        if '&' not in mml[-1]:  # 이미 타이 노트가 아닌 경우에만
                            mml[-1] = f"{mml[-1]}&{note_name}"
                            tie_note = True
            
            # MML 음표 추가 (타이 노트가 아닌 경우만)
            if not tie_note:
                # 길게 지속되는 음표는 타이 노트로 처리
                if note_duration > ticks_per_beat * 2:  # 2박자 이상이면 타이 노트로 분할
                    mml.append(f"{note_name}&{note_name}")
                else:
                    mml.append(f"{note_name}")
            
            # 다음 음표와의 연속성 확인
            if event_idx < len(events) - 1:
                next_event = None
                for future_idx in range(event_idx + 1, len(events)):
                    if events[future_idx]['type'] == 'note_on':
                        next_event = events[future_idx]
                        break
                
                if next_event:
                    # 다음 음표가 매우 빠르게 이어질 경우 (0.1박자 이내)
                    if next_event['time'] - event['time'] < ticks_per_beat * 0.1:
                        # 현재 음표 길이 짧게 조정 (다음 음과 자연스럽게 연결)
                        if note_duration > ticks_per_beat * 0.2:  # 충분히 길면
                            short_length = get_note_length(ticks_per_beat * 0.1, ticks_per_beat)
                            if short_length != current_length:
                                mml.append(f'L{short_length}')
                                current_length = short_length
                
            # 마지막 음표 정보 업데이트
            last_note_info = {
                'note': note, 
                'time': event['time'], 
                'length': note_duration,
                'name': note_name
            }
                
            previous_note = note
            processed_notes.add(note)  # 처리된 노트로 표시
            
        elif event['type'] == 'note_off':
            note = event['note']
            if note in notes_on:
                # 해당 노트 종료
                if note in active_notes:
                    active_notes.remove(note)
                del notes_on[note]
        
        previous_time = event['time']
    
    # MML 코드 정리 (연속된 동일 명령어 제거, 불필요한 볼륨 변경 제거 등)
    mml_string = ''.join(mml)
    
    # 불필요한 L 명령어 제거
    mml_string = re.sub(r'L(\d+\.?)L(\d+\.?)', r'L\2', mml_string)
    
    # 연속된 같은 옥타브 변경 최적화 (>>> -> >)
    for i in range(10, 0, -1):
        mml_string = mml_string.replace('>' * i, '>' * (i % 7))
        mml_string = mml_string.replace('<' * i, '<' * (i % 7))
    
    # 연속된 같은 볼륨 변경 제거
    mml_string = re.sub(r'V(\d+)V\1', r'V\1', mml_string)
    
    # 불필요한 반복을 제거하고 타이 노트로 처리
    mml_string = re.sub(r'([A-G][+\-]?)([A-G][+\-]?)(\1\2)+', r'\1\2&\1\2', mml_string)
    
    return mml_string

def midi_to_mml(midi_data):
    """MIDI 데이터를 멜로디/화음1/화음2로 나누어 MML로 변환"""
    # 바이트 데이터를 파일 객체로 변환
    midi_file = io.BytesIO(midi_data)
    mid = mido.MidiFile(file=midi_file)
    tempo = 120  # 기본 템포
    
    # MIDI 파일의 PPQ (펄스/분음표) 값
    ppq = mid.ticks_per_beat
    
    # 템포 정보 찾기
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                tempo = round(mido.tempo2bpm(msg.tempo))  # 템포를 정수로 반올림
                break
        if tempo != 120:  # 템포를 찾았으면 루프 종료
            break
    
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
        melody_mml = process_track(melody_track, mid.ticks_per_beat, ppq, is_harmony=False)
        tracks_mml.append(melody_mml)
    else:
        tracks_mml.append("")
        
    # 화음 트랙들 (두 번째와 세 번째 트랙)
    for i in range(1, min(3, len(tracks_info))):
        harmony_track = tracks_info[i]['track']
        harmony_mml = process_track(harmony_track, mid.ticks_per_beat, ppq, is_harmony=True)
        tracks_mml.append(harmony_mml)
    
    # 최대 3개 트랙까지만 사용
    while len(tracks_mml) < 3:
        tracks_mml.append("")
    
    # 각 트랙에 템포 정보 추가 및 1200자 제한 적용
    # 샘플처럼 템포 후 쉼표 추가 (T125R.)
    result = {
        "melody": f"T{tempo}R.{tracks_mml[0]}"[:1200],
        "harmony1": f"T{tempo}R.{tracks_mml[1]}"[:1200],
        "harmony2": f"T{tempo}R.{tracks_mml[2]}"[:1200]
    }
    
    return result

@app.route('/')
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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) 