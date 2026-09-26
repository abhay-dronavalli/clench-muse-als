import type { ActionName, Lang } from '../contracts'

type ToastText = Record<ActionName, (who: string) => string>

export const STRINGS = {
  en: {
    home: 'Home',
    connecting: 'Connecting to Clench…',
    speaking: 'Speaking…',
    hint: 'Clench = send  |  Double blink = cancel',
    confirm: {
      speak: 'Say this?',
      send_message: 'Send this message?',
      place_call: 'Place this call?',
      room_control: 'Do this?',
      help_alert: 'Call for help?',
    } satisfies Record<ActionName, string>,
    help: {
      title: 'Calling for help',
      cancel: 'Double blink to cancel',
    },
    toast: {
      ok: {
        speak: () => 'Said',
        send_message: (who) => `Message sent to ${who}`,
        place_call: (who) => `Calling ${who}`,
        room_control: () => 'Done',
        help_alert: (who) => `Calling ${who}`,
      } satisfies ToastText,
      demo: {
        speak: () => '(demo mode) would say it',
        send_message: (who) => `(demo mode) would message ${who}`,
        place_call: (who) => `(demo mode) would call ${who}`,
        room_control: () => '(demo mode) would do it',
        help_alert: (who) => `(demo mode) would call ${who}`,
      } satisfies ToastText,
      failed: "Couldn't send",
    },
  },
  es: {
    home: 'Inicio',
    connecting: 'Conectando con Clench…',
    speaking: 'Hablando…',
    hint: 'Apretar = enviar  |  Doble parpadeo = cancelar',
    confirm: {
      speak: '¿Decir esto?',
      send_message: '¿Enviar este mensaje?',
      place_call: '¿Hacer esta llamada?',
      room_control: '¿Hacer esto?',
      help_alert: '¿Pedir ayuda?',
    } satisfies Record<ActionName, string>,
    help: {
      title: 'Pidiendo ayuda',
      cancel: 'Doble parpadeo para cancelar',
    },
    toast: {
      ok: {
        speak: () => 'Dicho',
        send_message: (who) => `Mensaje enviado a ${who}`,
        place_call: (who) => `Llamando a ${who}`,
        room_control: () => 'Hecho',
        help_alert: (who) => `Llamando a ${who}`,
      } satisfies ToastText,
      demo: {
        speak: () => '(modo demo) lo diría',
        send_message: (who) => `(modo demo) enviaría un mensaje a ${who}`,
        place_call: (who) => `(modo demo) llamaría a ${who}`,
        room_control: () => '(modo demo) lo haría',
        help_alert: (who) => `(modo demo) llamaría a ${who}`,
      } satisfies ToastText,
      failed: 'No se pudo enviar',
    },
  },
} satisfies Record<Lang, unknown>
