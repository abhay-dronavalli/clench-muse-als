import type { ActionName, Lang } from '../contracts'

export const STRINGS = {
  en: {
    home: 'Home',
    connecting: 'Connecting to Clench…',
    speaking: 'Speaking…',
    hint: 'Clench = send  |  Double blink = cancel',
    confirm: {
      speak: 'Say this?',
      send_text: 'Send this text?',
      place_call: 'Place this call?',
      room_control: 'Do this?',
      help_alert: 'Call for help?',
    } satisfies Record<ActionName, string>,
  },
  es: {
    home: 'Inicio',
    connecting: 'Conectando con Clench…',
    speaking: 'Hablando…',
    hint: 'Apretar = enviar  |  Doble parpadeo = cancelar',
    confirm: {
      speak: '¿Decir esto?',
      send_text: '¿Enviar este mensaje?',
      place_call: '¿Hacer esta llamada?',
      room_control: '¿Hacer esto?',
      help_alert: '¿Pedir ayuda?',
    } satisfies Record<ActionName, string>,
  },
} satisfies Record<Lang, unknown>
