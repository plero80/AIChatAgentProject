import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { readChatPhotos, photoLimit, PHOTO_TYPES } from '../chatPhoto'
import Icon from './Icon'

export default function ChatInput({ onSend, disabled, imageUpload }) {
  const [value, setValue] = useState('')
  const [photos, setPhotos] = useState([])
  const [photoError, setPhotoError] = useState('')
  const [reading, setReading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef(null)
  const fileRef = useRef(null)
  const readController = useRef(null)
  const nextPhotoId = useRef(0)
  const busy = disabled || submitting
  const maxPhotos = photoLimit(imageUpload)

  useEffect(() => () => readController.current?.abort(), [])
  useLayoutEffect(() => {
    const input = inputRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 132)}px`
  }, [value])

  async function choosePhotos(event) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length || busy || reading) return
    readController.current?.abort()
    const controller = new AbortController()
    readController.current = controller
    setPhotoError('')
    setReading(true)
    try {
      const selected = await readChatPhotos(files, { existingCount: photos.length, options: imageUpload, signal: controller.signal })
      if (!controller.signal.aborted) {
        const additions = selected.map((photo) => ({ ...photo, id: nextPhotoId.current++ }))
        setPhotos((previous) => [...previous, ...additions])
      }
    } catch (error) {
      if (!controller.signal.aborted) setPhotoError(error.message)
    } finally {
      if (!controller.signal.aborted) setReading(false)
    }
  }

  function cancelRead() {
    readController.current?.abort()
    setPhotoError('')
    setReading(false)
  }

  function removePhoto(id) {
    setPhotos((previous) => previous.filter((photo) => photo.id !== id))
    setPhotoError('')
  }

  async function submit(event) {
    event.preventDefault()
    const text = value.trim()
    if ((!text && !photos.length) || busy || reading) return
    setSubmitting(true)
    try {
      if (await onSend(text, photos) !== false) {
        setValue('')
        setPhotos([])
        setPhotoError('')
      }
    } finally {
      setSubmitting(false)
      inputRef.current?.focus()
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      submit(event)
    }
  }

  return (
    <form className="composer" onSubmit={submit} aria-label="כתיבת הודעה">
      {photos.length > 0 && <div className="photo-previews" aria-label="תמונות מצורפות">
        {photos.map((photo, index) => <div key={photo.id} className="photo-preview">
          <img src={photo.preview} alt={`תמונה ${index + 1} שנבחרה לשליחה`} /><span title={photo.name}>{photo.name}</span>
          <button type="button" className="remove-photo" onClick={() => removePhoto(photo.id)} disabled={busy || reading} aria-label={`הסרת תמונה ${index + 1}`}><Icon name="close" /></button>
        </div>)}
      </div>}
      {reading && <div className="photo-upload-status"><span role="status">טוענים תמונות…</span><button type="button" className="cancel-photo-read" onClick={cancelRead}>ביטול</button></div>}
      {photoError && <p className="photo-error" role="alert">{photoError}</p>}
      <div className="composer-field">
        {imageUpload?.enabled && <>
          <input ref={fileRef} type="file" multiple accept={PHOTO_TYPES.join(',')} onChange={choosePhotos} disabled={busy || reading || photos.length >= maxPhotos} hidden aria-label="בחירת תמונות מצרכים" />
          <button type="button" className="attach-photo" onClick={() => fileRef.current?.click()} disabled={busy || reading || photos.length >= maxPhotos} aria-label="צירוף תמונות מצרכים" title={`עד ${maxPhotos} תמונות, עד 5 MB לכל תמונה`}><Icon name="photo" /></button>
        </>}
        <textarea ref={inputRef} value={value} disabled={busy} onChange={(e) => setValue(e.target.value)} onKeyDown={onKeyDown} placeholder="מה יש לך במקרר? מה בא לך להכין?" rows={1} maxLength={16000} aria-label="הודעה לעוזרת" aria-describedby="composer-hint" dir="auto" />
        <button type="submit" disabled={busy || reading || (!value.trim() && !photos.length)} aria-label="שליחת הודעה"><Icon name="send" /></button>
      </div>
      <div className="composer-footer" id="composer-hint"><span>{imageUpload?.enabled ? `טקסט ו/או עד ${maxPhotos} תמונות · ${photos.length}/${maxPhotos} מצורפות · 5 MB לתמונה` : 'קצת מצרכים, ונמצא יחד רעיון.'}</span><span className="keyboard-hint">Enter לשליחה <span>·</span> Shift + Enter לשורה חדשה</span></div>
    </form>
  )
}
