"""Validation and private storage for payment evidence images."""
import hashlib
import io
import os
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from inventory_commands import InventoryValidationError


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
EVIDENCE_DIR = Path(os.environ.get(
    'POPCORE_PAYMENT_EVIDENCE_DIR',
    Path(__file__).resolve().parent / 'uploads' / 'payment_evidence',
))
FORMATS = {
    'JPEG': ('image/jpeg', '.jpg'),
    'PNG': ('image/png', '.png'),
    'WEBP': ('image/webp', '.webp'),
}


def prepare_image(upload):
    if upload is None:
        raise InventoryValidationError('image is required', 'invalid_image')
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise InventoryValidationError('Image upload is too large', 'image_too_large')
    try:
        with Image.open(io.BytesIO(content)) as probe:
            image_format = probe.format
            if image_format not in FORMATS or getattr(probe, 'n_frames', 1) != 1:
                raise InventoryValidationError('Only still JPEG, PNG, or WebP images are allowed',
                                               'invalid_image')
            if probe.width * probe.height > MAX_IMAGE_PIXELS:
                raise InventoryValidationError('Image dimensions are too large', 'image_too_large')
            probe.verify()
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            mime, extension = FORMATS[image_format]
            output = io.BytesIO()
            if image_format == 'JPEG':
                image.convert('RGB').save(output, format='JPEG', quality=90, optimize=True)
            elif image_format == 'PNG':
                mode = 'RGBA' if 'A' in image.getbands() else 'RGB'
                image.convert(mode).save(output, format='PNG', optimize=True)
            else:
                mode = 'RGBA' if 'A' in image.getbands() else 'RGB'
                image.convert(mode).save(output, format='WEBP', quality=90, method=6)
    except InventoryValidationError:
        raise
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as exc:
        raise InventoryValidationError('Uploaded file is not a valid image',
                                       'invalid_image') from exc
    normalized = output.getvalue()
    if not normalized or len(normalized) > MAX_IMAGE_BYTES:
        raise InventoryValidationError('Normalized image is too large', 'image_too_large')
    object_id = f'{uuid.uuid4().hex}{extension}'
    return {
        'object_id': object_id, 'mime_type': mime, 'content': normalized,
        'byte_size': len(normalized),
        'content_hash': hashlib.sha256(normalized).hexdigest(),
    }


def publish(prepared):
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    final_path = EVIDENCE_DIR / prepared['object_id']
    part_path = final_path.with_suffix(final_path.suffix + '.part')
    with part_path.open('xb') as output:
        output.write(prepared['content'])
    os.replace(part_path, final_path)
    return final_path


def remove_unreferenced(path, con):
    if con.execute(
        'SELECT 1 FROM payment_evidence WHERE object_id=?', (path.name,)
    ).fetchone() is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def evidence_path(object_id):
    if Path(object_id).name != object_id:
        raise InventoryValidationError('Evidence object is invalid')
    return EVIDENCE_DIR / object_id
