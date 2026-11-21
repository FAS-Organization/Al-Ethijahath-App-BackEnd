for (const input of document.querySelectorAll('input[data-signature-pad]')) {
  if (!window.PointerEvent || !window.DataTransfer) continue
  const pad = document.createElement('canvas')
  const pen = pad.getContext('2d')
  if (!pen) continue
  pad.width = 600
  pad.height = 180
  pad.style.cssText = 'display:block;width:min(100%,360px);height:auto;touch-action:none;background:white;border:1px solid #777;border-radius:6px'
  pad.setAttribute('aria-label', 'Draw a signature here or use the image upload below')
  pen.strokeStyle = '#222'
  pen.fillStyle = '#222'
  pen.lineWidth = 3
  pen.lineCap = 'round'
  const clear = document.createElement('button')
  clear.type = 'button'
  clear.textContent = 'Clear signature'
  input.before(pad, clear)
  let pointer = null

  function position(event) {
    const rect = pad.getBoundingClientRect()
    return [(event.clientX - rect.left) * pad.width / rect.width,
            (event.clientY - rect.top) * pad.height / rect.height]
  }

  pad.addEventListener('pointerdown', event => {
    if (pointer !== null) return
    pointer = event.pointerId
    pad.setPointerCapture(pointer)
    const [x, y] = position(event)
    pen.beginPath()
    pen.arc(x, y, pen.lineWidth / 2, 0, Math.PI * 2)
    pen.fill()
    pen.beginPath()
    pen.moveTo(x, y)
  })
  pad.addEventListener('pointermove', event => {
    if (pointer !== event.pointerId) return
    pen.lineTo(...position(event))
    pen.stroke()
  })

  function finish(event) {
    if (pointer !== event.pointerId) return
    pointer = null
    const pixels = Uint8Array.from(atob(pad.toDataURL('image/png').split(',')[1]), ch => ch.charCodeAt(0))
    const drop = new DataTransfer()
    drop.items.add(new File([pixels], input.name + '.png', {type: 'image/png'}))
    input.files = drop.files
  }
  pad.addEventListener('pointerup', finish)
  pad.addEventListener('pointercancel', finish)
  clear.addEventListener('click', () => {
    pen.clearRect(0, 0, pad.width, pad.height)
    input.value = ''
  })
  input.addEventListener('change', () => pen.clearRect(0, 0, pad.width, pad.height))
}
