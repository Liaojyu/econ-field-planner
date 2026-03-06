"""
Parse Graduate_field_coures.pdf -> courses.json
Uses pdfplumber table extraction for accurate structure.
"""
import pdfplumber
import json
import re
from collections import defaultdict

PDF_PATH = "Graduate_field_coures.pdf"

FIELDS = [
    "總體與貨幣",
    "經濟發展與政策",
    "國際貿易與區域經濟",
    "國際金融",
    "環境健康經濟",
    "人力資源",
    "公共經濟",
    "產業組織",
    "計量經濟",
    "法律經濟",
    "財務經濟",
    "經濟史與方法論",
    "賽局與數理經濟",
]

# No course numbers are excluded — the note "選修數學系大學部課號，不得計入學門"
# applies to the math dept's *original* UG course codes, not the ECON cross-listed numbers.
EXCLUDED_NUMS: set = set()

# Strip this note from col0 before parsing the course name
NOTE_RE = re.compile(r'\s*選修數學系大學部課號[^。]*。?')

COURSE_NUM_RE = re.compile(
    r'\b(ECON\d{4,5}[A-Z]?|MATH\d{4}|STAT\d{4}|Fin\d{4}|323\s*[MU]\d{4})\b'
)
CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def normalize_num(num):
    num = re.sub(r'\s+', '', num)
    # Fix 5-digit ECON typo (ECON52299 -> ECON5229)
    m = re.match(r'(ECON)(\d{5})', num)
    if m:
        num = m.group(1) + m.group(2)[:4]
    return num


def extract_nums_from_cell(cell):
    if not cell:
        return []
    raw = COURSE_NUM_RE.findall(cell)
    return [normalize_num(n) for n in raw if normalize_num(n) not in EXCLUDED_NUMS]


def split_zh_en(text):
    """Split a concatenated Chinese+English name string."""
    text = re.sub(r'[＊＃]', '', text).strip()
    text = re.sub(r'\s+', ' ', text)

    # Find the last CJK character position, then find the first capital ASCII letter
    # that appears after it (may have non-CJK chars like ')', ' ' in between)
    cjk_positions = [m.start() for m in re.finditer(r'[\u4e00-\u9fff\u3400-\u4dbf]', text)]
    if cjk_positions:
        last_cjk = cjk_positions[-1]
        # Find first uppercase ASCII letter after last CJK char
        en_match = re.search(r'[A-Z]', text[last_cjk + 1:])
        if en_match:
            en_start = last_cjk + 1 + en_match.start()
            zh = text[:en_start].strip()
            en = text[en_start:].strip()
            return zh, en
        return text.strip(), ''

    if re.match(r'^[A-Z]', text):
        return '', text.strip()
    return text.strip(), ''


def is_field_header(text):
    """Return field name if row is a field section header, else None."""
    if not text:
        return None
    m = re.search(r'[『「]([^』」]+)[』」]', text)
    if m:
        cand = m.group(1).strip()
        for f in FIELDS:
            if f in cand:
                return f
    return None


def should_skip_row(row):
    """Return True for header/separator rows that carry no course data."""
    col0 = (row[0] or '').strip()
    col1 = (row[1] or '').strip() if len(row) > 1 else ''
    # Table header row
    if '課名Course Title' in col0:
        return True
    # Pure separator rows
    if not col0 and not col1:
        return True
    if col0 in ('', 'semester', 'course') and not col1:
        return True
    return False


def extract_all_rows():
    """Extract all table rows from the PDF with page context."""
    all_rows = []
    with pdfplumber.open(PDF_PATH) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    all_rows.append(row)
    return all_rows


def parse(rows):
    """
    Parse rows into: course_num -> {courseTitle, courseTitleEn, fields}

    Row patterns:
    A: [name+nums_combined, nums, ...] - normal
    B: [name_only, nums, ...]           - normal with multiple nums in col1
    C: [name_only, '', ...]
       [None, nums, ...]               - name and number on separate rows
    D: [None, more_nums, ...]          - continuation numbers
    E: [English_only, '', ...]         - English name continuation
    """
    courses = {}      # num -> {courseTitle, courseTitleEn, fields: []}
    current_field = None

    # State for building current course entry
    pending_zh = ''
    pending_en = ''
    pending_nums = []

    def flush(field):
        """Commit pending entry to courses under field."""
        if not pending_nums or not pending_zh:
            return
        for num in pending_nums:
            if num not in courses:
                courses[num] = {
                    'courseNumber': num,
                    'courseTitle': pending_zh,
                    'courseTitleEn': pending_en,
                    'fields': [],
                }
            if field and field not in courses[num]['fields']:
                courses[num]['fields'].append(field)

    def reset():
        nonlocal pending_zh, pending_en, pending_nums
        pending_zh = ''
        pending_en = ''
        pending_nums = []

    i = 0
    while i < len(rows):
        row = rows[i]
        i += 1

        # Normalize cells
        col0 = (row[0] or '').strip() if len(row) > 0 else ''
        col1 = (row[1] or '').strip() if len(row) > 1 else ''

        # Check for field header
        field = is_field_header(col0)
        if field:
            flush(current_field)
            reset()
            current_field = field
            continue

        if should_skip_row(row):
            continue

        if current_field is None:
            continue

        # --- Continuation row: col0 is None/empty, col1 has more numbers ---
        if not col0 and col1:
            nums = extract_nums_from_cell(col1)
            if nums:
                pending_nums.extend(nums)
            # Else col1 might be 'semester' etc — skip
            continue

        # --- English-only continuation row ---
        if col0 and not CJK_RE.search(col0) and re.match(r'^[A-Z]', col0) and not col1:
            if not pending_en:
                pending_en = col0
            continue

        # --- Normal course row: col0 has Chinese text ---
        if col0 and CJK_RE.search(col0):
            # Flush previous entry
            flush(current_field)
            reset()

            # Strip "選修數學系大學部課號…" note before parsing name
            col0_clean = NOTE_RE.sub('', col0).strip()
            zh, en = split_zh_en(col0_clean)
            pending_zh = zh
            pending_en = en

            # Extract numbers from col1
            pending_nums = extract_nums_from_cell(col1)
            continue

        # Other rows: skip
        continue

    # Flush last entry
    flush(current_field)
    return courses


def main():
    rows = extract_all_rows()
    courses = parse(rows)

    result = sorted(courses.values(), key=lambda x: x['courseNumber'])

    with open('courses.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # --- Verification stats ---
    print(f"Total courses: {len(result)}")

    field_counts = defaultdict(int)
    for c in result:
        for f in c['fields']:
            field_counts[f] += 1

    print("\nCourses per field:")
    for f in FIELDS:
        print(f"  {f}: {field_counts[f]}")

    print("\nCourses in 3+ fields:")
    for c in result:
        if len(c['fields']) >= 3:
            print(f"  {c['courseNumber']}: {c['courseTitle']} -> {c['fields']}")

    print("\nCourses with empty zh name:")
    for c in result:
        if not c['courseTitle']:
            print(f"  {c['courseNumber']}: en={c['courseTitleEn']}, fields={c['fields']}")

    print("\nCourses with empty fields:")
    for c in result:
        if not c['fields']:
            print(f"  {c['courseNumber']}: {c['courseTitle']}")


if __name__ == '__main__':
    main()
