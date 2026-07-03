import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib import request

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
STAMP = time.strftime('%Y%m%d_%H%M%S')
DEFAULT_JSON_OUT = ROOT / f'delta_study_courses_export_{STAMP}.json'
DEFAULT_CSV_OUT = ROOT / f'delta_study_links_export_{STAMP}.csv'
DEFAULT_APP_OUT = PROJECT_ROOT / 'public' / 'courses' / 'course-data.json'

BACKEND = 'https://backend.multistreaming.site/api/courses'
NOTES = 'https://gdgoenkaratia.com/api/courses'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
}

QUALITY_ORDER = {
    '1080p': 1080,
    '720p': 720,
    '480p': 480,
    '360p': 360,
    '240p': 240,
    '144p': 144,
}


def fetch_json(url, method='GET', body=None, extra_headers=None):
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode('utf-8'))


def course_summary(course):
    keys = [
        'id', 'title', 'short_description', 'description', 'courseHighlights',
        'price', 'discountPrice', 'isLive', 'isRecorded', 'status', 'priority',
        'banner', 'bannerSquare', 'validity', 'timeTable', 'facultyDetails',
        'faqs', 'createdAt', 'updatedAt'
    ]
    return {key: course.get(key) for key in keys if key in course}


def add_link(rows, course, link_type, url, **extra):
    if not url:
        return
    rows.append({
        'course_id': course.get('id', ''),
        'course_title': course.get('title', ''),
        'link_type': link_type,
        'topic': extra.get('topic', ''),
        'title': extra.get('title', ''),
        'teacher': extra.get('teacher', ''),
        'quality': extra.get('quality', ''),
        'status': extra.get('status', ''),
        'start_date': extra.get('start_date', ''),
        'end_date': extra.get('end_date', ''),
        'source': extra.get('source', ''),
        'url': url,
    })


def fetch_raw_export():
    courses_payload = fetch_json(f'{BACKEND}/')
    if courses_payload.get('state') != 200:
        raise RuntimeError(f"Course list failed: {courses_payload}")

    courses = sorted(courses_payload.get('data') or [], key=lambda c: c.get('priority', 999))
    export = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'course_count': len(courses),
        'endpoints': {
            'courses': f'{BACKEND}/',
            'detail': f'{BACKEND}/{{course_id}}',
            'classes': f'{BACKEND}/{{course_id}}/classes?populate=full',
            'pdfs': f'{NOTES}/{{course_id}}/pdfs?groupBy=topic',
            'todays_classes': f'{NOTES}/{{course_id}}/todays-classes',
        },
        'courses': [],
    }
    link_rows = []

    for index, course in enumerate(courses, start=1):
        course_id = course.get('id')
        if not course_id:
            continue

        record = {
            'course': course_summary(course),
            'detail': None,
            'classes_by_topic': [],
            'pdf_topics': [],
            'todays_classes': [],
            'errors': [],
        }

        try:
            detail_payload = fetch_json(f'{BACKEND}/{course_id}')
            if detail_payload.get('state') == 200:
                record['detail'] = detail_payload.get('data')
            else:
                record['errors'].append({'endpoint': 'detail', 'response': detail_payload})
        except Exception as exc:
            record['errors'].append({'endpoint': 'detail', 'error': str(exc)})

        try:
            classes_payload = fetch_json(f'{BACKEND}/{course_id}/classes?populate=full')
            if classes_payload.get('state') == 200:
                topics = (classes_payload.get('data') or {}).get('classes') or []
                record['classes_by_topic'] = topics
                collect_class_links(link_rows, course, topics)
            else:
                record['errors'].append({'endpoint': 'classes', 'response': classes_payload})
        except Exception as exc:
            record['errors'].append({'endpoint': 'classes', 'error': str(exc)})

        try:
            pdfs_payload = fetch_json(f'{NOTES}/{course_id}/pdfs?groupBy=topic')
            if pdfs_payload.get('state') == 200:
                topics = (pdfs_payload.get('data') or {}).get('topics') or []
                record['pdf_topics'] = topics
                collect_topic_pdf_links(link_rows, course, topics)
            else:
                record['errors'].append({'endpoint': 'pdfs', 'response': pdfs_payload})
        except Exception as exc:
            record['errors'].append({'endpoint': 'pdfs', 'error': str(exc)})

        try:
            today_payload = fetch_json(
                f'{NOTES}/{course_id}/todays-classes',
                method='POST',
                body={},
                extra_headers={'Origin': 'https://www.selectionway.com'},
            )
            if today_payload.get('state') == 200:
                today_classes = (today_payload.get('data') or {}).get('classes') or []
                record['todays_classes'] = today_classes
                collect_today_links(link_rows, course, today_classes)
            else:
                record['errors'].append({'endpoint': 'todays-classes', 'response': today_payload})
        except Exception as exc:
            record['errors'].append({'endpoint': 'todays-classes', 'error': str(exc)})

        export['courses'].append(record)
        print(f'[{index}/{len(courses)}] {course.get("title", course_id)} | links: {len(link_rows)}')

    return export, link_rows


def collect_class_links(link_rows, course, topics):
    for topic in topics:
        topic_name = topic.get('topicName', '')
        for cls in topic.get('classes') or []:
            status = cls.get('streamStatus') or ('live' if cls.get('isLive') else '')
            add_link(
                link_rows,
                course,
                'class_link',
                cls.get('class_link'),
                topic=topic_name,
                title=cls.get('title', ''),
                teacher=cls.get('teacherName', ''),
                status=status,
                start_date=cls.get('startDate', ''),
                end_date=cls.get('endDate', ''),
                source='classes',
            )
            for recording in cls.get('mp4Recordings') or []:
                add_link(
                    link_rows,
                    course,
                    'recording',
                    recording.get('url'),
                    topic=topic_name,
                    title=cls.get('title', ''),
                    teacher=cls.get('teacherName', ''),
                    quality=recording.get('quality', ''),
                    status=status,
                    start_date=cls.get('startDate', ''),
                    end_date=cls.get('endDate', ''),
                    source='classes.mp4Recordings',
                )
            for pdf in cls.get('classPdf') or []:
                add_link(
                    link_rows,
                    course,
                    'class_pdf',
                    pdf.get('url'),
                    topic=topic_name,
                    title=pdf.get('name') or cls.get('title', ''),
                    teacher=cls.get('teacherName', ''),
                    status=status,
                    start_date=cls.get('startDate', ''),
                    end_date=cls.get('endDate', ''),
                    source='classes.classPdf',
                )


def collect_topic_pdf_links(link_rows, course, topics):
    for topic in topics:
        topic_name = topic.get('topicName', '')
        for pdf in topic.get('pdfs') or []:
            add_link(
                link_rows,
                course,
                'topic_pdf',
                pdf.get('uploadPdf'),
                topic=topic_name,
                title=pdf.get('title', ''),
                teacher=pdf.get('teacherName', ''),
                source='pdfs.groupByTopic',
            )


def collect_today_links(link_rows, course, today_classes):
    for cls in today_classes:
        status = cls.get('streamStatus') or ('live' if cls.get('isLive') else '')
        add_link(
            link_rows,
            course,
            'today_class_link',
            cls.get('class_link'),
            title=cls.get('title', ''),
            teacher=cls.get('teacherName', ''),
            status=status,
            start_date=cls.get('startDate', ''),
            end_date=cls.get('endDate', ''),
            source='todays-classes',
        )
        for recording in cls.get('mp4Recordings') or []:
            add_link(
                link_rows,
                course,
                'today_recording',
                recording.get('url'),
                title=cls.get('title', ''),
                teacher=cls.get('teacherName', ''),
                quality=recording.get('quality', ''),
                status=status,
                start_date=cls.get('startDate', ''),
                end_date=cls.get('endDate', ''),
                source='todays-classes.mp4Recordings',
            )


def load_raw_export(path):
    with Path(path).open('r', encoding='utf-8') as file:
        return json.load(file)


def build_link_rows(export):
    rows = []
    for record in export.get('courses') or []:
        course = record.get('course') or {}
        collect_class_links(rows, course, record.get('classes_by_topic') or [])
        collect_topic_pdf_links(rows, course, record.get('pdf_topics') or [])
        collect_today_links(rows, course, record.get('todays_classes') or [])
    return rows


def slugify(value):
    slug = re.sub(r'[^\w\s-]+', '', value or '', flags=re.UNICODE).strip().lower()
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or 'course'


def text_value(value, fallback=''):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(str(item) for item in value if item is not None)
    if value is None:
        return fallback
    return str(value)


def quality_rank(recording):
    quality = str(recording.get('quality') or '').lower()
    if quality in QUALITY_ORDER:
        return QUALITY_ORDER[quality]
    match = re.search(r'(\d+)', quality)
    return int(match.group(1)) if match else 0


def normalize_recording(recording):
    return {
        'url': text_value(recording.get('url')),
        'quality': text_value(recording.get('quality'), 'MP4'),
        'size': recording.get('size') if isinstance(recording.get('size'), (int, float)) else 0,
        '_id': text_value(recording.get('_id') or recording.get('id')),
    }


def normalize_pdf(pdf, fallback_name='Class Notes'):
    url = text_value(pdf.get('url') or pdf.get('uploadPdf'))
    if not url:
        return None
    priority = pdf.get('priority')
    return {
        'priority': priority if isinstance(priority, int) else 1,
        'name': text_value(pdf.get('name') or pdf.get('title'), fallback_name),
        'url': url,
    }


def normalize_video(cls, topic, record_index):
    recordings = [
        normalize_recording(recording)
        for recording in cls.get('mp4Recordings') or []
        if recording.get('url')
    ]

    class_pdfs = []
    for pdf in cls.get('classPdf') or []:
        normalized_pdf = normalize_pdf(pdf, text_value(cls.get('title'), 'Class Notes'))
        if normalized_pdf:
            class_pdfs.append(normalized_pdf)
    class_pdfs.sort(key=lambda pdf: pdf['priority'])

    video_url = text_value(cls.get('class_link'))
    if not video_url and recordings:
        video_url = recordings[0]['url']

    if not video_url and not recordings and not class_pdfs:
        return None

    topic_id = topic.get('topicId') or (topic.get('topic') or {}).get('_id')
    return {
        'title': text_value(cls.get('title'), 'Untitled Class'),
        'description': text_value(cls.get('description')),
        'teacherName': text_value(cls.get('teacherName'), 'Unknown Teacher'),
        'duration': cls.get('duration') if isinstance(cls.get('duration'), (int, float)) else 0,
        'startDate': text_value(cls.get('startDate')),
        'endDate': text_value(cls.get('endDate')),
        'isFree': bool(cls.get('isFree')),
        'videoUrl': video_url,
        'mp4Recordings': recordings,
        'classPdf': class_pdfs,
        'source': {
            'courseId': text_value(cls.get('course')),
            'classId': text_value(cls.get('classId') or cls.get('_id') or cls.get('id')),
            'topicId': text_value(topic_id),
            'rawRecordIndex': record_index,
        },
    }


def icon_for_course(title):
    lowered = (title or '').lower()
    if 'english' in lowered:
        return '📖'
    if 'math' in lowered:
        return '🔢'
    if 'reasoning' in lowered:
        return '🧠'
    if 'science' in lowered:
        return '🔬'
    if 'vocab' in lowered:
        return '📝'
    if 'ssc' in lowered:
        return '🎓'
    if 'railway' in lowered:
        return '🚆'
    return '📚'


def normalize_bundle(export, include_inactive=False, raw_export_file=''):
    courses = []
    course_data = {}
    used_slugs = {}
    skipped_inactive = 0
    skipped_empty_rows = 0

    for record_index, record in enumerate(export.get('courses') or []):
        course = record.get('course') or {}
        detail = record.get('detail') or {}
        status = course.get('status') or detail.get('status') or ''
        if status != 'active' and not include_inactive:
            skipped_inactive += 1
            continue

        title = text_value(course.get('title') or detail.get('title'), 'Untitled Course')
        base_slug = slugify(title)
        slug_count = used_slugs.get(base_slug, 0) + 1
        used_slugs[base_slug] = slug_count
        course_id = base_slug if slug_count == 1 else f'{base_slug}-{slug_count}'

        videos = {}
        for topic in record.get('classes_by_topic') or []:
            topic_name = text_value(topic.get('topicName'), 'Uncategorized') or 'Uncategorized'
            normalized_videos = []
            for cls in topic.get('classes') or []:
                video = normalize_video(cls, topic, record_index)
                if video:
                    normalized_videos.append(video)
                else:
                    skipped_empty_rows += 1
            if normalized_videos:
                videos[topic_name] = normalized_videos

        subject_count = len(videos)
        video_count = sum(len(items) for items in videos.values())
        metadata = {
            'id': course_id,
            'sourceId': text_value(course.get('id') or detail.get('id')),
            'name': title,
            'icon': icon_for_course(title),
            'description': text_value(course.get('short_description') or detail.get('short_description') or course.get('description') or detail.get('description')),
            'banner': course.get('banner') or detail.get('banner') or None,
            'status': status,
            'price': course.get('price') if isinstance(course.get('price'), (int, float)) else detail.get('price'),
            'discountPrice': course.get('discountPrice') if isinstance(course.get('discountPrice'), (int, float)) else detail.get('discountPrice'),
            'validity': text_value(course.get('validity') or detail.get('validity')),
            'subjectCount': subject_count,
            'videoCount': video_count,
            'dataFile': f'courses/{course_id}.json',
        }
        courses.append(metadata)
        course_data[course_id] = {
            'courseName': title,
            'extractedAt': export.get('generated_at') or '',
            'videos': videos,
        }

    bundle = {
        'generatedAt': export.get('generated_at') or '',
        'source': {
            'mode': 'all' if include_inactive else 'active',
            'rawExportFile': str(raw_export_file) if raw_export_file else '',
            'rawCourseCount': len(export.get('courses') or []),
            'appCourseCount': len(courses),
        },
        'courses': courses,
        'courseData': course_data,
        'stats': {
            'appVideoCount': sum(course.get('videoCount', 0) for course in courses),
            'skippedInactiveCourses': skipped_inactive,
            'skippedEmptyRows': skipped_empty_rows,
        },
    }
    return bundle


def write_json(path, data, *, indent=2, atomic=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path.with_suffix(path.suffix + '.tmp') if atomic else path
    with target.open('w', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=indent)
    if atomic:
        target.replace(path)


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        'course_id', 'course_title', 'link_type', 'topic', 'title', 'teacher',
        'quality', 'status', 'start_date', 'end_date', 'source', 'url'
    ]
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_chunked_bundle(courses_dir, bundle):
    courses_dir = Path(courses_dir)
    courses_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        'generatedAt': bundle['generatedAt'],
        'source': bundle['source'],
        'courses': bundle['courses'],
        'stats': bundle['stats'],
    }
    write_json(courses_dir / 'courses.json', manifest, indent=2, atomic=True)

    course_count = len(bundle['courseData'])
    for idx, (course_id, data) in enumerate(bundle['courseData'].items(), start=1):
        write_json(courses_dir / f'{course_id}.json', data, indent=2, atomic=True)
        print(f'  [{idx}/{course_count}] wrote {course_id}.json')


def parse_args():
    parser = argparse.ArgumentParser(description='Export Delta Study course data and generate EduVerse app data.')
    parser.add_argument('--from-raw', help='Generate app data from an existing raw export JSON without fetching live APIs.')
    parser.add_argument('--include-inactive', action='store_true', help='Include inactive courses in the app data bundle.')
    parser.add_argument('--output-app', default=str(DEFAULT_APP_OUT), help='Path for normalized app bundle JSON (legacy single-file).')
    parser.add_argument('--raw-json-out', default=str(DEFAULT_JSON_OUT), help='Path for timestamped raw export JSON.')
    parser.add_argument('--csv-out', default=str(DEFAULT_CSV_OUT), help='Path for flattened link CSV export.')
    parser.add_argument('--courses-dir', default=str(PROJECT_ROOT / 'public' / 'courses'), help='Directory for chunked per-course JSON files.')
    parser.add_argument('--legacy-bundle', action='store_true', help='Also write the old single-file bundle for backward compat.')
    return parser.parse_args()


def main():
    args = parse_args()
    raw_json_path = Path(args.from_raw) if args.from_raw else Path(args.raw_json_out)

    if args.from_raw:
        export = load_raw_export(raw_json_path)
        link_rows = build_link_rows(export)
    else:
        export, link_rows = fetch_raw_export()
        write_json(raw_json_path, export, indent=2)

    csv_path = Path(args.csv_out)
    write_csv(csv_path, link_rows)

    bundle = normalize_bundle(export, include_inactive=args.include_inactive, raw_export_file=raw_json_path)

    courses_dir = Path(args.courses_dir)
    write_chunked_bundle(courses_dir, bundle)

    if args.legacy_bundle:
        app_path = Path(args.output_app)
        write_json(app_path, bundle, indent=2, atomic=True)

    courses_with_errors = sum(1 for course in export.get('courses') or [] if course.get('errors'))
    app_json_path = courses_dir / 'courses.json'
    summary = {
        'raw_json_file': str(raw_json_path),
        'csv_file': str(csv_path),
        'manifest_file': str(app_json_path),
        'courses_dir': str(courses_dir),
        'raw_courses': len(export.get('courses') or []),
        'app_courses': len(bundle['courses']),
        'app_videos': bundle['stats']['appVideoCount'],
        'skipped_inactive_courses': bundle['stats']['skippedInactiveCourses'],
        'skipped_empty_rows': bundle['stats']['skippedEmptyRows'],
        'links': len(link_rows),
        'courses_with_errors': courses_with_errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
