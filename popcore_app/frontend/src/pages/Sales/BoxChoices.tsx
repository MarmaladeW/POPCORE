import { useState } from 'react'
import { BOX_CHOICES, boxChoiceNote, type BoxChoice, type BoxOptions } from './boxChoice'
import './Checkout.css'

/** One question per blind box over half a set: where did these boxes come from? Answered before payment
 *  (owner, 2026-10-27). With several opened sets, the cashier picks which one. */
export function BoxQuestionButtons({ name, quantity, setSize, fromDisplay, options, choice, openSetId, disabled, answer }: {
  name: string; quantity: number; setSize: number; fromDisplay: boolean; options: BoxOptions | null
  choice: BoxChoice | null; openSetId: number | null; disabled: boolean
  answer: (choice: BoxChoice, openSetId?: number) => void
}) {
  const [picking, setPicking] = useState(false)
  const sets = options?.open_sets ?? []
  // A display piece is sold on its own: only loose boxes can answer for it.
  const shown = BOX_CHOICES.filter(item => !fromDisplay || item.value === 'loose')
  const unavailable = (value: BoxChoice) => value === 'fresh_set' ? !options?.fresh_set : value === 'open_set' ? !sets.length : false
  function choose(value: BoxChoice) {
    if (value !== 'open_set') { setPicking(false); answer(value); return }
    if (sets.length === 1) { setPicking(false); answer(value, sets[0].id); return }
    setPicking(true)
  }
  return <fieldset className="co-methods co-boxes" disabled={disabled}>
    <legend>盲盒从哪里拿的？ Where did these {quantity} boxes come from? <span className="co-muted">{name} · a set has {setSize}</span></legend>
    <div>{shown.map(item => <button key={item.value} type="button" aria-pressed={choice === item.value} disabled={unavailable(item.value)}
      onClick={() => choose(item.value)}><span className="co-method-zh">{item.zh}</span> <span className="co-method-en">{item.label}</span><small>{boxChoiceNote(item.value, options)}</small></button>)}</div>
    {(picking || (choice === 'open_set' && sets.length > 1)) && <div className="co-box-sets" role="group" aria-label="Which opened set">
      {sets.map(set => <button key={set.id} type="button" aria-pressed={choice === 'open_set' && openSetId === set.id}
        onClick={() => { setPicking(false); answer('open_set', set.id) }}>Opened set #{set.id} · {set.remaining_qty} left</button>)}
    </div>}
  </fieldset>
}

export default function BoxChoices({ questions, required, disabled, answer }: {
  questions: { product_id: number; from_display: boolean; name: string; quantity: number; set_size: number; choice: BoxChoice | null; open_set_id: number | null; options: BoxOptions | null }[]
  required: boolean; disabled: boolean
  answer: (body: { product_id: number; from_display: boolean; choice: BoxChoice; open_set_id?: number }) => void
}) {
  if (!questions.length) return null
  return <section className="co-box-questions" aria-label="Blind boxes">
    {questions.map(item => <BoxQuestionButtons key={`${item.product_id}|${item.from_display}`} name={item.name} quantity={item.quantity}
      setSize={item.set_size} fromDisplay={item.from_display} options={item.options} choice={item.choice} openSetId={item.open_set_id}
      disabled={disabled} answer={(choice, openSetId) => answer({ product_id: item.product_id, from_display: item.from_display, choice, ...(openSetId ? { open_set_id: openSetId } : {}) })} />)}
    {required && <p className="co-instruction" role="status">Answer for the blind boxes first. Payment opens once each one is answered.</p>}
  </section>
}
