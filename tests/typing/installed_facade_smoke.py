"""Static-only smoke for the installed root and explicit API facades."""

from hwpx.document import HwpxDocument
from typing_extensions import assert_type

from hwpx_automation import create_document_from_plan as root_create
from hwpx_automation.api import create_document_from_plan as api_create

root_document = root_create({})
api_document = api_create({})

assert_type(root_document, HwpxDocument)
assert_type(api_document, HwpxDocument)
