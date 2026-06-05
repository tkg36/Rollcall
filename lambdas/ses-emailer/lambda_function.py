import json
import os
import boto3
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import urllib.parse

s3 = boto3.client("s3")
ses = boto3.client("ses")

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def parse_event(event):
    record = event["Records"][0]
    body = record.get("body")
    return json.loads(body) if isinstance(body, str) else body


def download_file_from_s3(bucket, key, local_path="/tmp/report.xlsx"):
    s3.download_file(bucket, key, local_path)
    return local_path


def send_email_with_attachment(to_email, subject, body, file_path):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SENDER_EMAIL")
    msg["To"] = to_email

    # Body
    msg.attach(MIMEText(body, "plain"))

    # Attachment
    with open(file_path, "rb") as f:
        part = MIMEApplication(f.read(), _subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    part.add_header(
        "Content-Disposition",
        "attachment",
        filename=file_path.split("/")[-1]
    )

    msg.attach(part)

    return ses.send_raw_email(
        Source=msg["From"],
        Destinations=[to_email],
        RawMessage={"Data": msg.as_string()}
    )


def lambda_handler(event, context):
    logger.info(f"Event: {json.dumps(event, indent=2)}")

    record = event["Records"][0]

    # S3 bucket + key
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]

    # S3 URL encoding fix
    key = urllib.parse.unquote_plus(key)

    to_email = key.split("/")[0]
    file_key = key.split("/")[1]

    logger.info(f"Emailing {file_key} from {bucket}/{key} to {to_email}")

    subject = "Pipeline Complete"
    body = "See attached file."

    # 1. download from S3
    local_file = download_file_from_s3(bucket, key)
    logger.info(f"Downloaded file to {local_file}")

    # 2. send email
    response = send_email_with_attachment(
        to_email=to_email,
        subject=subject,
        body=body,
        file_path=local_file
    )
    logger.info(f"Email sent: {response}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Email sent successfully",
            "to": to_email,
            "s3": f"s3://{bucket}/{key}",
            "sesMessageId": response["MessageId"]
        })
    }