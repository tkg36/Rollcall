import io
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
import pandas as pd

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET_NAME  = os.environ.get("CSV_BUCKET")
DEPTS_BUCKET = os.environ.get("DEPTS_BUCKET")
TMP_DIR      = "/tmp" if os.environ.get("AWS_EXECUTION_ENV") else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tmp"
)

MD1_NAME = os.environ.get("MD1_NAME", "")

FILE_PREFIXES = {
    "unfilled":          os.environ.get("FILE_PREFIX_UNFILLED"),
    "contractor_open":   os.environ.get("FILE_PREFIX_CONTRACTOR_OPEN"),
    "contractor_closed": os.environ.get("FILE_PREFIX_CONTRACTOR_CLOSED"),
    "candidates":        os.environ.get("FILE_PREFIX_CANDIDATES"),
}

FILTER_CONFIG = {
    "unfilled": {
        "sheet":          "Sheet1",
        "anchor_columns": ["Subdivision", "Requisition Number"],
        "filter_column":  "Subdivision",
        "filter_value":   "Chief Information Security Office",
    },
    "contractor_open": {
        "sheet":          "Open",
        "anchor_columns": ["Sub-Division", "Req #"],
        "filter_column":  "Sub-Division",
        "filter_value":   "ES&F",
    },
    "contractor_closed": {
        "sheet":          "Closed",
        "anchor_columns": ["Sub-Division", "Req #"],
        "filter_column":  "Sub-Division",
        "filter_value":   "ES&F",
    },
}

ESF_WF_FILE = "ESF WF data file ref.xlsx"
CC_ID_FILE  = "cc_id.csv"
DEPTS_FILE  = "depts.csv"
STATUS_FILE = "status.csv"
OUTPUT_KEY  = "ESF WF data file.xlsx"

MASTER_COLUMNS = [
    "Existing v New",
    "Department",
    "Worker Type",
    "Job Code",
    "Job Profile",
    "Cost Center ID",
    "Grade Level",
    "Management",
    "Manager Name",
    "MD-1",
    "MD-2",
    "Status",
    "Req #",
    "FTE",
    "Location",
    "Note",
    "Hire Name",
    "Start Date",
    "State",
    "Job Requisition Primary Location (Building)",
    "Job Requisition Additional Locations",
    "Comment",
    "Report Status",
    "Contractor Req Status",
    "Req Status",
]


# --- File discovery and download ---

def get_newest_file_by_prefix(bucket_name, prefix):
    response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
    matches = [obj for obj in response.get("Contents", []) if obj["Key"].endswith(".xlsx")]
    if not matches:
        raise FileNotFoundError(f"No .xlsx files found with prefix: {prefix}")
    newest = max(matches, key=lambda x: x["LastModified"])
    logger.info(f"Resolved '{prefix}' -> '{newest['Key']}'")
    return newest["Key"]


def discover_files(bucket_name):
    with ThreadPoolExecutor() as executor:
        futures = {
            name: executor.submit(get_newest_file_by_prefix, bucket_name, prefix)
            for name, prefix in FILE_PREFIXES.items()
        }
        return {name: fut.result() for name, fut in futures.items()}


def download_file(bucket_name, key):
    local_path = os.path.join(TMP_DIR, key.split("/")[-1])
    s3.download_file(bucket_name, key, local_path)
    logger.info(f"Downloaded s3://{bucket_name}/{key} -> {local_path}")
    return local_path


def download_all_files(bucket_name, discovered):
    os.makedirs(TMP_DIR, exist_ok=True)
    with ThreadPoolExecutor() as executor:
        futures = {
            name: executor.submit(download_file, bucket_name, key)
            for name, key in discovered.items()
        }
        return {name: fut.result() for name, fut in futures.items()}


# --- Reference data ---

def load_reference_data(bucket_name):
    def fetch(key):
        return s3.get_object(Bucket=bucket_name, Key=key)["Body"].read()

    with ThreadPoolExecutor() as executor:
        futures = {
            "cc_id":  executor.submit(fetch, CC_ID_FILE),
            "depts":  executor.submit(fetch, DEPTS_FILE),
            "status": executor.submit(fetch, STATUS_FILE),
            "esf_wf": executor.submit(fetch, ESF_WF_FILE),
        }
        raw = {k: fut.result() for k, fut in futures.items()}

    cc_id_df = pd.read_csv(io.StringIO(raw["cc_id"].decode("utf-8")))
    cc_id_df["cc_id"] = pd.to_numeric(cc_id_df["cc_id"], errors="coerce")

    depts_df  = pd.read_csv(io.StringIO(raw["depts"].decode("utf-8")))
    status_df = pd.read_csv(io.StringIO(raw["status"].decode("utf-8")))

    esf_bytes    = io.BytesIO(raw["esf_wf"])
    esf_reqs_df  = pd.read_excel(esf_bytes, sheet_name="Reqs")
    esf_reqs_df  = esf_reqs_df.loc[:, ~esf_reqs_df.columns.str.startswith("Unnamed")]
    esf_reqs_df["Req #"] = pd.to_numeric(esf_reqs_df["Req #"], errors="coerce")

    esf_bytes.seek(0)
    esf_all_df = pd.read_excel(esf_bytes, sheet_name="ALL")

    logger.info(
        f"Reference loaded — cc_id: {len(cc_id_df)}, depts: {len(depts_df)}, "
        f"status: {len(status_df)}, esf_reqs: {len(esf_reqs_df)}, esf_all: {len(esf_all_df)}"
    )
    return {
        "cc_id":    cc_id_df,
        "depts":    depts_df,
        "status":   status_df,
        "esf_reqs": esf_reqs_df,
        "esf_all":  esf_all_df,
    }


# --- Filtering ---

def find_header_row(local_path, known_columns, sheet_name=None):
    df = pd.read_excel(local_path, sheet_name=sheet_name, header=None)
    for i, row in df.iterrows():
        if all(col in row.values for col in known_columns):
            return i + 1
    raise ValueError(f"Header row not found. Expected columns: {known_columns}")


def load_and_filter(local_path, sheet_name, anchor_columns, filter_column, filter_value):
    header_row = find_header_row(local_path, anchor_columns, sheet_name)
    df = pd.read_excel(local_path, sheet_name=sheet_name, header=header_row - 1)
    filtered = df[df[filter_column] == filter_value]
    logger.info(
        f"{os.path.basename(local_path)} ({sheet_name}): "
        f"{len(df)} rows -> {len(filtered)} after '{filter_column}' == '{filter_value}'"
    )
    return filtered


def filter_all_files(local_files):
    return {
        name: load_and_filter(
            local_files[name],
            cfg["sheet"],
            cfg["anchor_columns"],
            cfg["filter_column"],
            cfg["filter_value"],
        )
        for name, cfg in FILTER_CONFIG.items()
    }


def filter_candidates(local_path, dept_codes):
    header_row = find_header_row(local_path, ["Candidate Name", "Candidate Status"], sheet_name="Sheet1")
    df = pd.read_excel(local_path, sheet_name="Sheet1", header=header_row - 1)
    df["cc_match"] = pd.to_numeric(
        df["Cost Center"].astype(str).str.strip().str[:4], errors="coerce"
    )
    filtered = df[df["cc_match"].isin(dept_codes)].drop(columns=["cc_match"]).dropna(how="all")
    logger.info(f"Candidates: {len(df)} rows -> {len(filtered)} after dept code filter")
    return filtered


# --- Shared lookup ---

def resolve_dept_and_md2(cost_center_num, cc_id_df, depts_df):
    dept_match = cc_id_df[cc_id_df["cc_id"] == cost_center_num]
    department = dept_match.iloc[0]["subdepartment"] if not dept_match.empty else ""
    md2_match  = depts_df[depts_df["department"] == department]
    md2        = md2_match.iloc[0]["MD-2"] if not md2_match.empty else ""
    return department, md2


def grade_to_management(grade_level):
    return "Management" if "M" in str(grade_level) else "Non Management"


# --- Output builders ---

def build_crew_unfilled(filtered, ref):
    df         = filtered["unfilled"].copy()
    candidates = filtered["candidates"].copy()
    cc_id      = ref["cc_id"]
    depts      = ref["depts"]
    esf_reqs   = ref["esf_reqs"].copy()

    esf_reqs["Req #"] = pd.to_numeric(esf_reqs["Req #"], errors="coerce")
    req_check = set(esf_reqs["Req #"].dropna().astype(int))

    candidates["req_int"] = pd.to_numeric(
        candidates["Job Requisition"].astype(str).str.strip().str[:7], errors="coerce"
    )

    rows = []
    for _, row in df.iterrows():
        req_num = row.get("Requisition Number")
        if pd.isna(req_num):
            continue
        req_num_int = int(req_num)

        cost_center     = row.get("Cost Center ID", "")
        department, md2 = resolve_dept_and_md2(pd.to_numeric(cost_center, errors="coerce"), cc_id, depts)

        hire_match = candidates[
            (candidates["req_int"] == req_num_int) &
            (candidates["Candidate Status"] == "Ready for Hire")
        ]

        rows.append({
            "Existing v New":                              "Existing" if req_num_int in req_check else "NEW",
            "Department":                                  department,
            "Worker Type":                                 "Regular",
            "Job Code":                                    row.get("ID", ""),
            "Job Profile":                                 row.get("Job Profile Name", ""),
            "Cost Center ID":                              cost_center,
            "Grade Level":                                 row.get("Grade Grouping - GTA", ""),
            "Management":                                  grade_to_management(row.get("Grade Grouping - GTA", "")),
            "Manager Name":                                row.get("Hiring Manager Name", ""),
            "MD-1":                                        MD1_NAME,
            "MD-2":                                        md2,
            "Status":                                      row.get("Job Requisition Status", ""),
            "Req #":                                       req_num_int,
            "FTE":                                         row.get("Number of Openings Total", ""),
            "Location":                                    "",
            "Note":                                        "",
            "Hire Name":                                   hire_match.iloc[0]["Candidate Name"] if not hire_match.empty else "",
            "Start Date":                                  "",
            "State":                                       row.get("State", ""),
            "Job Requisition Primary Location (Building)": row.get("Job Requisition Primary Location (Building)", ""),
            "Job Requisition Additional Locations":        row.get("Job Requisition Additional Locations", ""),
            "Comment":                                     "",
        })

    result = pd.DataFrame(rows)
    logger.info(f"Crew Unfilled: {len(result)} rows")
    return result


def build_crew_filled(filtered, ref):
    candidates = filtered["candidates"].copy()
    cc_id      = ref["cc_id"]
    depts      = ref["depts"]
    esf_reqs   = ref["esf_reqs"].copy()
    status_map = dict(zip(ref["status"]["status"], ref["status"]["short status"]))

    esf_reqs["Req #"] = pd.to_numeric(esf_reqs["Req #"], errors="coerce")
    req_check         = set(esf_reqs["Req #"].dropna().astype(int))
    esf_indexed       = esf_reqs.dropna(subset=["Req #"]).set_index("Req #")

    cutoff_date = pd.Timestamp.today() - pd.Timedelta(days=6)
    candidates["req_int"] = pd.to_numeric(
        candidates["Job Requisition"].astype(str).str.strip().str[:7], errors="coerce"
    )

    start_dates = pd.to_datetime(candidates["Candidate Start Date"], errors="coerce")
    df = candidates[
        candidates["req_int"].isin(req_check) &
        candidates["Candidate Status"].isin([
            "Offer", "Employment Agreement", "Ready for Hire", "Background Check"
        ]) &
        ((start_dates >= cutoff_date) | (candidates["Candidate Start Date"] == 0) | start_dates.isna())
    ].copy()

    rows = []
    for _, row in df.iterrows():
        req_num = row.get("req_int")
        if pd.isna(req_num):
            continue
        req_num_int = int(req_num)

        cost_center_4   = str(row.get("Cost Center", "")).strip()[:4]
        department, md2 = resolve_dept_and_md2(pd.to_numeric(cost_center_4, errors="coerce"), cc_id, depts)

        hire_name  = row.get("Candidate Name", "")
        start_date = row.get("Candidate Start Date", "")

        if req_num_int in esf_indexed.index:
            esf_row        = esf_indexed.loc[req_num_int]
            job_code       = esf_row.get("Job Code", "")
            job_profile    = esf_row.get("Job Profile", "")
            comment        = esf_row.get("Comment", "")
            esf_hire       = esf_row.get("Hire Name", "")
            esf_start      = esf_row.get("Start Date", "")
            if str(esf_hire) != str(hire_name):
                existing_v_new = "Update"
            elif str(esf_start) != str(start_date):
                existing_v_new = "Update Date"
            else:
                existing_v_new = "Existing"
        else:
            job_code = job_profile = comment = ""
            existing_v_new = "Existing"

        rows.append({
            "Existing v New":                              existing_v_new,
            "Department":                                  department,
            "Worker Type":                                 "Regular",
            "Job Code":                                    job_code,
            "Job Profile":                                 job_profile,
            "Cost Center ID":                              cost_center_4,
            "Grade Level":                                 row.get("Grade", ""),
            "Management":                                  grade_to_management(row.get("Grade", "")),
            "Manager Name":                                row.get("Hiring Manager", ""),
            "MD-1":                                        MD1_NAME,
            "MD-2":                                        md2,
            "Status":                                      status_map.get(row.get("Candidate Status", ""), ""),
            "Req #":                                       req_num_int,
            "FTE":                                         "",
            "Location":                                    "Crew",
            "Note":                                        "",
            "Hire Name":                                   hire_name,
            "Start Date":                                  start_date,
            "State":                                       row.get("State", ""),
            "Job Requisition Primary Location (Building)": row.get("Job Requisition Primary Location", ""),
            "Job Requisition Additional Locations":        "",
            "Comment":                                     comment,
        })

    result = pd.DataFrame(rows)
    logger.info(f"Crew Filled: {len(result)} rows")
    return result


def build_contractor_unfilled(filtered, ref):
    df        = filtered["contractor_open"].copy()
    cc_id     = ref["cc_id"]
    depts     = ref["depts"]
    esf_reqs  = ref["esf_reqs"].copy()
    status_map = dict(zip(ref["status"]["status"], ref["status"]["short status"]))

    df.columns = [col.replace("\n", " ").strip() for col in df.columns]

    esf_reqs["Req #"] = esf_reqs["Req #"].astype(str).str.strip()
    req_exists_set    = set(esf_reqs["Req #"].dropna())
    req_to_hire       = (
        esf_reqs.dropna(subset=["Req #"]).set_index("Req #")["Hire Name"].to_dict()
        if "Hire Name" in esf_reqs.columns else {}
    )

    rows = []
    for idx, row in df.iterrows():
        try:
            req_num = row.get("Req #")
            if pd.isna(req_num):
                continue
            req_num_str = str(req_num).strip()
            if not req_num_str:
                continue

            cost_center = row.get("Cost Center", "")
            try:
                cc_num = pd.to_numeric(cost_center, errors="coerce")
                department, md2 = ("", "") if pd.isna(cc_num) else resolve_dept_and_md2(cc_num, cc_id, depts)
            except Exception:
                department = md2 = ""

            if req_num_str not in req_exists_set:
                existing_v_new = "NEW"
            else:
                esf_hire = req_to_hire.get(req_num_str, "")
                existing_v_new = "Open" if (pd.isna(esf_hire) or esf_hire == "") else "Filled"

            loc           = str(row.get("LOC", "")).strip()
            state         = {"PA": "Pennsylvania", "TX": "Texas"}.get(loc, loc)
            report_status = row.get("Status (Please Make a Selection from List)", "")

            rows.append({
                "Existing v New":                              existing_v_new,
                "Department":                                  department,
                "Worker Type":                                 "Contractor" if cost_center else "",
                "Job Code":                                    "",
                "Job Profile":                                 row.get("Job Title (Standardized)", ""),
                "Cost Center ID":                              cost_center,
                "Grade Level":                                 row.get("Grade Level", ""),
                "Management":                                  row.get("Management", ""),
                "Manager Name":                                row.get("Hiring Manager", ""),
                "MD-1":                                        row.get("MD-1", ""),
                "MD-2":                                        md2,
                "Status":                                      status_map.get(str(report_status), ""),
                "Req #":                                       req_num_str,
                "FTE":                                         row.get("FTE", ""),
                "Location":                                    row.get("LOC", ""),
                "Note":                                        "",
                "Hire Name":                                   "",
                "Start Date":                                  "",
                "State":                                       state,
                "Job Requisition Primary Location (Building)": "",
                "Job Requisition Additional Locations":        "",
                "Comment":                                     "",
                "Report Status":                               report_status,
            })
        except Exception as e:
            logger.error(f"Skipping contractor_open row {idx}: {e}")

    result = pd.DataFrame(rows)
    logger.info(f"Contractor Unfilled: {len(result)} rows")
    return result


def build_contractor_filled(filtered, ref):
    df        = filtered["contractor_closed"].copy()
    cc_id     = ref["cc_id"].copy()
    depts     = ref["depts"]
    esf_reqs  = ref["esf_reqs"].copy()
    esf_all   = ref["esf_all"]
    status_map = dict(zip(ref["status"]["status"], ref["status"]["short status"]))

    df.columns = [
        col.replace("\n", " ").replace("\r", " ").strip() if isinstance(col, str) else col
        for col in df.columns
    ]
    logger.info(f"Contractor closed columns: {list(df.columns)}")

    filled_col = "Filled: F Cancelled : C"
    status_col = "Status (Please Make a Selection from List)"

    cutoff_date   = pd.Timestamp.today() - pd.Timedelta(days=10)
    df["Start Date"] = pd.to_datetime(df["Start Date"], errors="coerce")
    df = df[
        (df["Start Date"] >= cutoff_date) &
        (df[filled_col].astype(str).str.upper().str.strip() == "F")
    ].copy()

    def _norm(val):
        s = str(val).strip()
        return "" if (not s or s.lower() == "nan") else s

    esf_reqs["Req #"] = esf_reqs["Req #"].apply(_norm)
    req_exists_set    = set(esf_reqs["Req #"].dropna())
    req_to_hire       = (
        esf_reqs.dropna(subset=["Req #"]).set_index("Req #")["Hire Name"].to_dict()
        if "Hire Name" in esf_reqs.columns else {}
    )

    cc_id["cc_id"]  = pd.to_numeric(cc_id["cc_id"], errors="coerce")
    cc_to_subdept   = cc_id.dropna(subset=["cc_id"]).set_index("cc_id")["subdepartment"].to_dict()
    dept_to_md2     = depts.dropna(subset=["department"]).set_index("department")["MD-2"].to_dict()
    infosys_lookup  = esf_all.dropna(subset=["Manager Name"]).set_index("Manager Name")["Department"].to_dict()

    rows = []
    for _, row in df.iterrows():
        req_num_str = _norm(row.get("Req #", ""))
        if not req_num_str:
            continue

        cost_center = row.get("Cost Center", "")
        manager     = row.get("Hiring Manager", "")
        start_date  = row.get("Start Date")
        dept_head   = row.get("Dept Head", "")

        try:
            cc_key  = int(str(cost_center).strip()[:4])
            subdept = cc_to_subdept.get(cc_key)
            if subdept is None:
                department = ""
            elif subdept == "Infosys":
                department = infosys_lookup.get(manager, "Infosys")
            else:
                department = subdept
        except (ValueError, TypeError):
            department = ""

        md2 = dept_to_md2.get(department)
        md2 = str(md2) if md2 is not None else (str(dept_head) if dept_head else "")

        hire_name         = req_to_hire.get(req_num_str, "")
        contractor_status = row.get(status_col, "")

        if req_num_str not in req_exists_set:
            col_a_status = (
                "Validate if started"
                if pd.notna(start_date) and start_date < pd.Timestamp.today()
                else "NEW"
            )
        else:
            col_a_status = "Newly Filled" if (pd.isna(hire_name) or hire_name == "") else "Filled"

        loc   = str(row.get("LOC", "")).strip()
        state = {"PA": "Pennsylvania", "TX": "Texas"}.get(loc, loc)

        rows.append({
            "Existing v New":                              "",
            "Status":                                      col_a_status,
            "Department":                                  department,
            "Worker Type":                                 "Contractor" if cost_center else "",
            "Job Code":                                    "",
            "Job Profile":                                 row.get("Job Tile (Standardized)", ""),
            "Cost Center ID":                              cost_center,
            "Grade Level":                                 row.get("Grade Level", ""),
            "Management":                                  row.get("Management", ""),
            "Manager Name":                                manager,
            "MD-1":                                        row.get("MD-1", ""),
            "MD-2":                                        md2,
            "Req Status":                                  status_map.get(str(contractor_status), ""),
            "Req #":                                       req_num_str,
            "FTE":                                         row.get("FTE", ""),
            "Location":                                    row.get("LOC", ""),
            "Note":                                        "",
            "Hire Name":                                   hire_name,
            "Start Date":                                  start_date,
            "State":                                       state,
            "Job Requisition Primary Location (Building)": "",
            "Job Requisition Additional Locations":        "",
            "Comment":                                     "",
            "Contractor Req Status":                       contractor_status,
        })

    result = pd.DataFrame(rows)
    logger.info(f"Contractor Filled: {len(result)} rows")
    return result


# --- Output workbook ---

def to_master_columns(df):
    for col in MASTER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[MASTER_COLUMNS]


def write_output_workbook(crew_unfilled, crew_filled, contractor_unfilled, contractor_filled, ret_addr):
    output_path = os.path.join(TMP_DIR, OUTPUT_KEY)

    try:
        response  = s3.get_object(Bucket=DEPTS_BUCKET, Key=OUTPUT_KEY)
        xl        = pd.ExcelFile(io.BytesIO(response["Body"].read()))
        sheet     = "Output" if "Output" in xl.sheet_names else ("Reqs" if "Reqs" in xl.sheet_names else None)
        prev_df   = pd.read_excel(xl, sheet_name=sheet) if sheet else pd.DataFrame(columns=MASTER_COLUMNS)
        logger.info(f"Loaded previous run from sheet '{sheet}': {len(prev_df)} rows")
    except s3.exceptions.NoSuchKey:
        prev_df = pd.DataFrame(columns=MASTER_COLUMNS)
        logger.info("No previous file — starting fresh")

    prev_df["Req #"] = prev_df["Req #"].astype(str).str.strip()

    new_df = pd.concat(
        [to_master_columns(df) for df in [crew_unfilled, crew_filled, contractor_unfilled, contractor_filled]],
        ignore_index=True,
    )
    new_df["Req #"] = new_df["Req #"].astype(str).str.strip()

    new_req_nums    = set(new_df["Req #"].dropna())
    carried_forward = prev_df[~prev_df["Req #"].isin(new_req_nums)].copy()
    carried_forward["Existing v New"] = "Carried Forward"

    combined = to_master_columns(pd.concat([new_df, carried_forward], ignore_index=True))
    logger.info(f"Output: {len(new_df)} new + {len(carried_forward)} carried forward = {len(combined)} total rows")

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        combined.to_excel(writer, sheet_name="Output", index=False)

    dest_key = f"{ret_addr}/{OUTPUT_KEY}"
    s3.upload_file(output_path, DEPTS_BUCKET, dest_key)
    logger.info(f"Uploaded to s3://{DEPTS_BUCKET}/{dest_key}")
    return output_path


# --- Entry point ---

def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event, indent=2)}")
    ret_addr = json.loads(event["Records"][0].get("body", "{}")).get("retAddr")
    logger.info(f"Return address: {ret_addr}")

    # Load reference data in the background while discovering and downloading input files
    with ThreadPoolExecutor(max_workers=1) as executor:
        ref_future  = executor.submit(load_reference_data, DEPTS_BUCKET)
        discovered  = discover_files(BUCKET_NAME)
        local_files = download_all_files(BUCKET_NAME, discovered)
        ref         = ref_future.result()

    filtered = filter_all_files(local_files)
    dept_codes = set(pd.to_numeric(ref["cc_id"]["cc_id"], errors="coerce").dropna().astype(int))
    filtered["candidates"] = filter_candidates(local_files["candidates"], dept_codes)

    crew_unfilled       = build_crew_unfilled(filtered, ref)
    crew_filled         = build_crew_filled(filtered, ref)
    contractor_unfilled = build_contractor_unfilled(filtered, ref)
    contractor_filled   = build_contractor_filled(filtered, ref)

    output_path = write_output_workbook(
        crew_unfilled, crew_filled, contractor_unfilled, contractor_filled, ret_addr
    )

    logger.info(f"Complete. Output: {output_path}")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Headcount reconciliation complete",
            "retAddr": ret_addr,
            "bucket":  DEPTS_BUCKET,
            "key":     OUTPUT_KEY,
        }),
    }
