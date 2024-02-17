from io import BytesIO
from typing import TYPE_CHECKING

from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import event_source, S3Event
import boto3

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

from PIL import Image


@event_source(data_class=S3Event)
def handler(event: S3Event, _: LambdaContext) -> None:
    s3_client: S3Client = boto3.client("s3")

    bucket = event.bucket_name
    key = event.object_key

    tag_response = s3_client.get_object_tagging(Bucket=bucket, Key=key)
    compression_tag = [
        pair for pair in tag_response["TagSet"] if pair["Key"] == "compressed"
    ]
    if len(compression_tag) > 0 and compression_tag[0]["Value"] == "true":
        return

    object = s3_client.get_object(Bucket=bucket, Key=key)
    body = object["Body"].read()

    image_buffer = BytesIO()

    with Image.open(BytesIO(body)) as image_orig:
        image_orig.save(image_buffer, format="jpeg", quality=30)

    image_buffer.seek(0)

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=image_buffer,
        ContentType="image/jpeg",
        Tagging="compressed=true",
    )

    image_buffer.close()
