import assert from 'node:assert/strict'
import test from 'node:test'
import { locationProblemText, mapLink, parseCoordinates } from './location.ts'

test('a pin copied from Google Maps is read as the store spot', () => {
  assert.deepEqual(parseCoordinates('43.65320, -79.38318'), { lat: 43.6532, lng: -79.38318 })
  assert.deepEqual(parseCoordinates(' 43.8561,-79.3370 '), { lat: 43.8561, lng: -79.337 })
  assert.deepEqual(parseCoordinates('43.8561 -79.3370'), { lat: 43.8561, lng: -79.337 })
  assert.deepEqual(parseCoordinates('43.8561，-79.3370'), { lat: 43.8561, lng: -79.337 })
  for (const text of ['', 'Downtown', '43.65', '95, -79.38', '43.65, -190', '43.65, -79.38, 5']) {
    assert.equal(parseCoordinates(text), null, text)
  }
})

test('every location problem says what to do in Chinese first', () => {
  for (const problem of ['denied', 'timeout', 'unavailable', 'unsupported'] as const) {
    assert.match(locationProblemText(problem), /^[一-鿿]/)
    assert.match(locationProblemText(problem), / · [A-Z]/)
  }
  assert.equal(mapLink({ lat: 43.6532, lng: -79.3832 }), 'https://www.google.com/maps?q=43.6532,-79.3832')
})
