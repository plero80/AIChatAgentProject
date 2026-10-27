# Iteration 11 — up to ten photos per message

## Current input

> It doesn't have to be only one photo. The user should be able to send up to max 10 photos. And we should support it

## Situation before this change

Iteration 10 accepted one optional `image` per chat message. The upload control,
guard adapter and recognition service each handled one photo. The normal workflow
already consumed the user's text plus recognized ingredients, and checkpoints
stored that combined text without image bytes.

The single attachment field and request-size budget prevented a user from sending
several ingredient photos in one turn.

## Implemented solution

Both `/chat` and `/api/clients/{slug}/chat` now accept `images`, with at most ten
attachments in one message:

```json
{
  "message": "I want a salty recipe",
  "images": [
    { "mime_type": "image/jpeg", "data": "BASE64_FIRST_PHOTO" },
    { "mime_type": "image/png", "data": "BASE64_SECOND_PHOTO" }
  ]
}
```

Text-only requests omit `images` or send an empty array. Photo-only requests omit
`message` or send an empty string. An empty message with no photos is rejected.
The previous `image` field remains accepted and is converted to a one-element
collection. Sending both `image` and `images` is rejected to avoid ambiguity.

## Processing steps

1. The frontend allows multiple selection and adding photos over several
   selections. It shows individual previews, remove buttons and an attachment
   count. It stops accepting additions at ten; removing one makes space again.
2. Each selected batch is checked before reading any files. An invalid file or
   excess count rejects that selection with a visible error while preserving
   already attached photos. Cancelling a read discards the incomplete selection.
   A failed send retains the text and attachments for retry.
3. React sends all photos in one request. Local filenames, preview URLs and UI
   identifiers are excluded from the JSON.
4. The backend enforces ten photos independently of the browser. Each must be an
   actual JPEG, PNG or WebP still image, at most 5 MB and 20 megapixels. The body
   budget now accommodates ten maximum-size photos plus base64 and text overhead.
   An invalid batch returns 422 before guard, recognition or graph execution;
   an oversized HTTP body returns 413.
5. A worker normalizes the photos sequentially, preserving attachment order,
   correcting orientation, removing metadata and resizing each to fit 1536 pixels
   per side. Sequential decoding avoids holding ten full-resolution decoded
   images in memory at once.
6. One guard request includes the user's text and every normalized photo. Its
   instructions evaluate individual images and their combination. A rejected
   request cannot advance the business graph.
7. After approval, one recognition request includes all photos and the text. The
   prompt asks for combined ingredient observations, separates uncertainty, and
   discourages duplicates or quantity claims from repeated views of the same
   ingredient. This is one model result for the message, not ten independent turns.
8. The input processor appends the observations to the user's text, then removes
   the images before entering the existing workflow. For example, the preference
   `salty` and ingredients from several photos reach the same recipe analysis.
   Checkpoints retain observations for follow-ups, without uploaded image bytes.

## Configuration and compatibility

The public `image_upload` configuration now advertises `max_count: 10`, the
per-photo byte limit, the total byte limit and accepted MIME types.

This uses the existing configurable image recognition service and model. There is
no database migration or new graph-state field. Photo-enabled published releases
support the new collection when the backend and frontend are updated. Releases
without `image_recognition` remain text-only, including conversations pinned to
those releases. Existing single-photo callers continue working.

Custom `BaseImageRecognizer` implementations must now accept
`recognize(images, message)`, where `images` is a tuple of photos. Published
configuration JSON retains its existing shape.

## Pros and cons

Advantages:

- Users can combine ingredients from several photos with one text preference.
- One shared guard and recognition step serves both file and database routes.
- Graph topology, state schemas, checkpoint keys and runtime-cache keys stay usable.
- A batch is processed as a whole, with no silently dropped photos or partial
  conversation writes when validation or recognition fails.

Tradeoffs:

- More photos increase upload size, image-token cost and possible latency, even
  though there is only one guard call and one recognition call per admitted turn.
- JSON/base64 adds about one-third to binary size. Ten 5 MB photos produce roughly
  67 MB of encoded image data; any hosting proxy must permit that request size.
  The application buffers this bounded request in memory.
- Recognition can still miss or confuse ingredients. The output remains a bounded
  summary (40 ingredient names, 20 uncertain items), rather than an exhaustive
  inventory with separate findings for each photo.
- Photos are not archived. Follow-ups use the saved observations; inspecting the
  original images again requires uploading them again.

## Verification

Verified on 2026-10-26: **415 backend unit/regression tests, 13 isolated PostgreSQL
integration tests, and 43 frontend tests passed**. Frontend lint and the production
build also passed. Local reports are `tmp/multi-photo-unit.xml` and
`tmp/multi-photo-postgres.xml`.

Backend checks cover ten distinct photos with and without text; ordered delivery
to the guard and recognition adapter; compatibility with `image`; rejection of
an eleventh image, an invalid final attachment and mixed attachment fields; body
size budgeting; and checkpoints without image bytes. Provider calls use offline
HTTP mocks, including the installed OpenAI SDK's multi-image serialization.

Frontend checks cover selection across batches, limits before file reads, batch
cancellation, transport of all ten photos, and rendering every attached preview.
The PostgreSQL integration test exercises the database route, release capability,
conversation pinning and checkpoint contents with ten photos.

No live provider calls are required by these tests; recognition quality and live
latency on real ingredient photos have not been evaluated in this change.

Key files: [message contract](../../backend/models/chat_input.py),
[guard adapter](../../backend/guards/decisions.py),
[recognizer](../../backend/services/chat/image_recognizer.py),
[input processor](../../backend/workflows/image_input.py),
[upload reader](../../Frontend/src/chatPhoto.js), and
[composer](../../Frontend/src/components/ChatInput.jsx).
