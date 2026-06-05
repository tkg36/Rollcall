// index.js
const AWS = require("aws-sdk");
const { simpleParser } = require("mailparser");

// AWS clients
const s3 = new AWS.S3();
const sns = new AWS.SNS();

// Environment variables
const CSV_BUCKET = process.env.CSV_BUCKET;       // e.g., "rollcall-s3-csv"
const SNS_TOPIC_ARN = process.env.SNS_TOPIC_ARN; // e.g., "arn:aws:sns:us-east-1:844905860028:dataReceived"

// Simple logger
const logger = {
  info: (...args) => console.log("[INFO]", ...args),
  error: (...args) => console.error("[ERROR]", ...args),
};

exports.handler = async (event) => {
  logger.info("Event received:", JSON.stringify(event, null, 2));

  try {
    const record = event.Records[0].s3;
    const bucket = record.bucket.name;
    const key = decodeURIComponent(record.object.key.replace(/\+/g, " "));

    logger.info(`Processing email from bucket: ${bucket}, key: ${key}`);

    // 1️⃣ Get the raw email from S3
    const emailObj = await s3.getObject({ Bucket: bucket, Key: key }).promise();
    const rawEmail = emailObj.Body;

    // 2️⃣ Parse the email using mailparser
    const parsed = await simpleParser(rawEmail);

    const csvFiles = [];

    if (parsed.attachments && parsed.attachments.length > 0) {
      for (const attachment of parsed.attachments) {
        const filename = attachment.filename;
        if (filename && filename.toLowerCase().endsWith(".csv")) {
          const csvKey = `csv/${filename}`;

          await s3.putObject({
            Bucket: CSV_BUCKET,
            Key: csvKey,
            Body: attachment.content,
            ContentType: "text/csv"
          }).promise();

          csvFiles.push(csvKey);
          logger.info(`Saved CSV attachment: ${csvKey}`);
        }
      }
    }

    // 3️⃣ Optionally publish to SNS
    if (csvFiles.length > 0 && SNS_TOPIC_ARN) {
      await sns.publish({
        TopicArn: SNS_TOPIC_ARN,
        Message: `Extracted CSV files: ${csvFiles.join(", ")}`
      }).promise();

      logger.info(`Published SNS notification for CSV files: ${csvFiles.join(", ")}`);
    }

    logger.info(`Finished processing email: ${key}`);
    return { status: "success", files: csvFiles };
  } catch (err) {
    logger.error("Error processing email:", err);
    throw err;
  }
};