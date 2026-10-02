/**
 * Fixture for the exam-prep screens: an EGEL Plus ISOFT-shaped goal (fake gateway only).
 *
 * The blueprint numbers are the real ones (Guía para el sustentante, Ceneval, julio 2024:
 * 4 areas, 14 subáreas, 143 Disciplinar items). The questions are ORIGINAL and deliberately
 * small - a real question bank stays in the gitignored data/ folder and is not copied here. They
 * follow the exam's format: a short situated case, always three options (A, B, C), one key.
 *
 * Each subárea has two option sets of neighbouring terms and four cues each; the first three
 * cues of a subárea are ordinary practice items and the other five are sealed for mocks
 * (pool `mock`), so the fake has 42 practice + 70 sealed items and a 60-question mock is
 * reachable. Only ./gateway.ts and ./exam.ts import this file (both are the mock chunk).
 */

import type { Goal } from '@/lib/types';
import type { MockCard, MockPractice, MockTable } from './fixtures';

/** A neutral demo deadline: `days` after the day the mock loads, as a local YYYY-MM-DD. */
function demoDeadline(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const ISOFT_GOAL: Goal = {
  goal_id: 'egel-isoft',
  title: 'EGEL Plus ISOFT - Disciplinar',
  concept: 'Ingeniería de software: análisis, diseño, desarrollo y gestión de proyectos',
  depth: 'apply',
  purpose: 'Ejemplo: preparar la sección disciplinar del EGEL Plus ISOFT.',
  deadline: demoDeadline(45),
  minutes_per_session: 60,
  sessions_per_week: 4,
  assessment: 'EGEL Plus ISOFT: 143 reactivos disciplinares, tres opciones cada uno.',
  source_priority: 'alignment',
  target_capabilities: ['apply'],
  transfer_required: false,
  domain: 'procedural',
  created_at: '2026-09-15',
};

type Cue = [stem: string, answer: 0 | 1 | 2, why: string];

interface OptionSet {
  options: [string, string, string];
  ask: string;
  cues: Cue[];
}

interface SubareaSpec {
  ref: string;
  title: string;
  items: number;
  sets: [OptionSet, OptionSet];
}

interface AreaSpec {
  code: string;
  title: string;
  subareas: SubareaSpec[];
}

export const ISOFT_AREAS: AreaSpec[] = [
  {
    code: '1',
    title: 'Análisis de Sistemas de Software',
    subareas: [
      {
        ref: '1.1',
        title: 'Tipos de requerimientos',
        items: 12,
        sets: [
          {
            options: ['Requerimiento funcional', 'Requerimiento no funcional', 'Restricción legal'],
            ask: '¿Cómo se clasifica este requerimiento?',
            cues: [
              ['Una cadena de farmacias en Mérida necesita que el sistema emita la receta electrónica al cerrar la venta.', 0, 'Describe un servicio que el sistema debe proveer: es funcional.'],
              ['La app de una aseguradora debe responder cualquier consulta de póliza en menos de 2 segundos con 500 usuarios concurrentes.', 1, 'Fija un umbral medible de desempeño: no funcional (eficiencia de desempeño).'],
              ['El expediente clínico debe conservarse al menos cinco años, como exige una norma oficial mexicana.', 2, 'La obligación nace de una norma: restricción legal, no una función ni un atributo de calidad.'],
              ['El portal de una universidad en Monterrey debe permitir al alumno descargar su kárdex.', 0, 'Es algo que el sistema hace para el usuario: funcional.'],
            ],
          },
          {
            options: ['Requerimiento de negocio', 'Requerimiento de usuario', 'Requerimiento de sistema'],
            ask: '¿En qué nivel de la jerarquía de requerimientos se ubica?',
            cues: [
              ['La dirección de una distribuidora quiere reducir 15 % las mermas de inventario este año.', 0, 'Expresa un objetivo de la organización, no una función del software.'],
              ['El almacenista quiere registrar entradas y salidas de mercancía desde su celular.', 1, 'Lenguaje natural, desde la perspectiva de quien usa el sistema.'],
              ['El módulo de inventario debe validar el SKU contra el catálogo maestro y rechazar códigos de más de 14 dígitos.', 2, 'Detalle funcional preciso, escrito para el equipo técnico.'],
              ['Un despacho contable quiere presentar a tiempo todas sus declaraciones mensuales.', 0, 'Es una meta organizacional: requerimiento de negocio.'],
            ],
          },
        ],
      },
      {
        ref: '1.2',
        title: 'Técnicas y herramientas para la obtención, análisis, priorización y validación',
        items: 9,
        sets: [
          {
            options: ['Entrevista', 'Cuestionario', 'Observación en sitio'],
            ask: '¿Qué técnica de obtención conviene?',
            cues: [
              ['Hay 600 cajeros en 40 sucursales y solo dos semanas para cuantificar qué funciones usan.', 1, 'Muchos informantes dispersos y poco tiempo: cuestionario.'],
              ['El jefe de crédito es el único que conoce las reglas de excepción y tiene una hora libre esta semana.', 0, 'Un experto clave y detalle fino: entrevista.'],
              ['Los capturistas dicen que siguen el manual, pero nadie sabe explicar cómo resuelven los casos raros.', 2, 'Conocimiento tácito: se descubre observando el trabajo real.'],
              ['Se necesita la opinión de 3,000 clientes sobre la nueva app bancaria.', 1, 'Población grande y datos cuantificables: cuestionario.'],
            ],
          },
          {
            options: ['MoSCoW', 'Modelo de Kano', 'Prototipo desechable'],
            ask: '¿Qué técnica aplica?',
            cues: [
              ['Hay que decidir qué entra en la versión que sale en seis semanas y qué se pospone.', 0, 'Must/Should/Could/Won\'t fija el alcance de una entrega con fecha fija.'],
              ['Se quiere saber qué atributos generan entusiasmo y cuáles solo se dan por sentados.', 1, 'Kano clasifica atributos por su efecto en la satisfacción.'],
              ['El usuario no logra describir la pantalla de captura y hay que validar el flujo antes de construir.', 2, 'Un prototipo desechable valida requerimientos difusos antes de construir.'],
              ['El comité debe separar lo imprescindible de lo deseable para un lanzamiento con fecha fija.', 0, 'Priorización para una fecha fija: MoSCoW.'],
            ],
          },
        ],
      },
      {
        ref: '1.3',
        title: 'Técnicas y herramientas de documentación',
        items: 10,
        sets: [
          {
            options: ['Diagrama de casos de uso', 'Diagrama de actividades', 'Diagrama de máquina de estados'],
            ask: '¿Qué diagrama UML conviene?',
            cues: [
              ['Mostrar qué actores interactúan con el sistema de citas médicas y qué objetivos persiguen.', 0, 'Actores y objetivos: casos de uso.'],
              ['Modelar el flujo de aprobación de una orden de compra con decisiones y actividades en paralelo.', 1, 'Flujo de un proceso con decisiones y paralelismo: actividades.'],
              ['Describir los estados de un pedido (creado, pagado, enviado, entregado) y los eventos que los cambian.', 2, 'Ciclo de vida de un objeto: máquina de estados.'],
              ['Comunicar al cliente el alcance funcional del sistema en una sola vista.', 0, 'El alcance funcional de un vistazo: casos de uso.'],
            ],
          },
          {
            options: ['Matriz de trazabilidad', 'Especificación IEEE 830 (SRS)', 'Historia de usuario'],
            ask: '¿Qué artefacto se usa?',
            cues: [
              ['Hay que saber qué pruebas y módulos se afectan si cambia el requerimiento RF-12.', 0, 'El análisis de impacto se hace con la matriz de trazabilidad.'],
              ['Un contrato con gobierno exige un documento formal y completo de requisitos antes de construir.', 1, 'Documento formal y completo: SRS según IEEE 830.'],
              ['Un equipo Scrum registra "como cajero quiero reimprimir un ticket para atender reclamos".', 2, 'Formato rol-meta-beneficio: historia de usuario.'],
              ['El auditor quiere comprobar que cada requerimiento tiene al menos un caso de prueba asociado.', 0, 'Requerimiento contra prueba: matriz de trazabilidad.'],
            ],
          },
        ],
      },
    ],
  },
  {
    code: '2',
    title: 'Diseño de Sistemas de Software',
    subareas: [
      {
        ref: '2.1',
        title: 'Diseño arquitectónico de software',
        items: 10,
        sets: [
          {
            options: ['Cliente-servidor', 'En capas', 'Microservicios'],
            ask: '¿Qué arquitectura conviene?',
            cues: [
              ['Una tienda en línea debe escalar solo el catálogo en temporada alta y desplegar cada módulo por separado.', 2, 'Escalado y despliegue independientes por módulo: microservicios.'],
              ['Se quiere aislar presentación, lógica y acceso a datos para cambiar la base de datos sin tocar la interfaz.', 1, 'Separación por responsabilidades con dependencias hacia abajo: capas.'],
              ['Varias terminales de captura consultan un servidor central de base de datos en la misma oficina.', 0, 'Clientes que piden servicios a un servidor central: cliente-servidor.'],
              ['Cada equipo debe publicar su servicio con su propia base de datos y su propio ciclo de liberación.', 2, 'Autonomía de datos y de liberación: microservicios.'],
            ],
          },
          {
            options: ['Disponibilidad', 'Escalabilidad', 'Seguridad'],
            ask: '¿Qué atributo de calidad expresa este requerimiento arquitectónico?',
            cues: [
              ['El sistema de urgencias debe operar 99.9 % del tiempo aunque falle un servidor.', 0, 'Tiempo en operación ante fallas: disponibilidad.'],
              ['La tienda debe atender el triple de usuarios en temporada sin rediseñar el sistema.', 1, 'Crecer en carga sin rediseño: escalabilidad.'],
              ['Solo el personal autorizado consulta los expedientes y cada acceso queda registrado.', 2, 'Control de acceso y bitácora: seguridad.'],
              ['Si cae el centro de datos principal, el secundario toma el control en menos de un minuto.', 0, 'Conmutación por falla: disponibilidad.'],
            ],
          },
        ],
      },
      {
        ref: '2.2',
        title: 'Diseño de módulos, componentes y de datos',
        items: 16,
        sets: [
          {
            options: ['Singleton', 'Observer', 'Strategy'],
            ask: '¿Qué patrón de diseño aplica?',
            cues: [
              ['Debe existir una sola instancia del administrador de configuración en toda la aplicación.', 0, 'Una sola instancia con acceso global: Singleton.'],
              ['Cuando cambia el precio de un producto, varias pantallas deben actualizarse automáticamente.', 1, 'Suscriptores notificados ante un cambio: Observer.'],
              ['El costo de envío cambia según la paquetería y se quiere intercambiar el algoritmo en tiempo de ejecución.', 2, 'Algoritmos intercambiables detrás de una interfaz: Strategy.'],
              ['Varios módulos deben suscribirse a los eventos de un sensor sin que el sensor los conozca.', 1, 'Publicador desacoplado de sus suscriptores: Observer.'],
            ],
          },
          {
            options: ['Primera forma normal (1FN)', 'Segunda forma normal (2FN)', 'Tercera forma normal (3FN)'],
            ask: '¿Qué forma normal se viola?',
            cues: [
              ['La tabla Cliente guarda en un solo campo "tel1, tel2, tel3".', 0, 'Un atributo multivaluado rompe la atomicidad: 1FN.'],
              ['En Detalle(pedido, producto, cantidad, nombre_producto), el nombre depende solo de producto, parte de la llave.', 1, 'Dependencia parcial de la llave compuesta: 2FN.'],
              ['En Empleado(id, depto_id, depto_nombre), el nombre del departamento depende de depto_id y no de la llave.', 2, 'Dependencia transitiva: 3FN.'],
              ['Un campo "materias" contiene la lista de materias inscritas separadas por comas.', 0, 'Valores repetidos en un campo: 1FN.'],
            ],
          },
        ],
      },
      {
        ref: '2.3',
        title: 'Diseño de interfaces',
        items: 7,
        sets: [
          {
            options: ['Visibilidad del estado del sistema', 'Prevención de errores', 'Consistencia y estándares'],
            ask: '¿Qué heurística de Nielsen se atiende?',
            cues: [
              ['Al subir un archivo grande se muestra una barra de progreso con el tiempo restante.', 0, 'El sistema informa qué está pasando: visibilidad del estado.'],
              ['El botón "Eliminar cuenta" pide confirmar escribiendo el nombre de la cuenta.', 1, 'Evita el error antes de que ocurra: prevención de errores.'],
              ['Todas las pantallas colocan "Guardar" en la misma posición y con el mismo ícono.', 2, 'Mismas convenciones en todo el sistema: consistencia.'],
              ['El calendario de reservaciones desactiva las fechas pasadas.', 1, 'No deja elegir una opción inválida: prevención de errores.'],
            ],
          },
          {
            options: ['Prototipo de baja fidelidad', 'Prueba de usabilidad', 'Guía de estilo'],
            ask: '¿Qué conviene usar?',
            cues: [
              ['Al inicio se quieren explorar tres distribuciones de pantalla en papel en una tarde.', 0, 'Explorar barato y rápido: baja fidelidad.'],
              ['Cinco usuarios reales intentan completar una compra mientras el equipo observa dónde se atoran.', 1, 'Usuarios reales ejecutando tareas: prueba de usabilidad.'],
              ['Tres equipos construyen módulos y deben usar los mismos colores, tipografías y componentes.', 2, 'Reglas visuales compartidas: guía de estilo.'],
              ['Se quiere medir cuánto tardan los usuarios en encontrar el botón de pago.', 1, 'Medir tareas con usuarios: prueba de usabilidad.'],
            ],
          },
        ],
      },
    ],
  },
  {
    code: '3',
    title: 'Desarrollo de Sistemas de Software',
    subareas: [
      {
        ref: '3.1',
        title: 'Lenguajes de desarrollo de software',
        items: 10,
        sets: [
          {
            options: ['Compilado a código nativo', 'Interpretado', 'Compilado a bytecode para una máquina virtual'],
            ask: '¿Cómo se ejecuta el lenguaje descrito?',
            cues: [
              ['C genera un ejecutable para el procesador antes de correr.', 0, 'Traducción previa a código máquina: compilado a nativo.'],
              ['Un script de Bash se lee y ejecuta línea por línea sin compilarlo antes.', 1, 'Sin paso de compilación previo: interpretado.'],
              ['Java se traduce a archivos .class y la JVM los ejecuta en cualquier sistema operativo.', 2, 'Bytecode sobre la JVM.'],
              ['C# se compila a IL y el CLR de .NET lo ejecuta.', 2, 'Lenguaje intermedio sobre una máquina virtual (CLR).'],
            ],
          },
          {
            options: ['Tipado estático', 'Tipado dinámico', 'Inferencia de tipos'],
            ask: '¿Qué característica del sistema de tipos se describe?',
            cues: [
              ['El compilador rechaza asignar un texto a una variable declarada como int.', 0, 'El tipo se comprueba al compilar: estático.'],
              ['En Python una misma variable guarda un número y después una cadena.', 1, 'El tipo pertenece al valor, no a la variable: dinámico.'],
              ['En Kotlin, val total = 10 declara un Int sin escribir el tipo.', 2, 'El compilador deduce el tipo: inferencia.'],
              ['El error de tipo aparece solo cuando se ejecuta esa línea del programa.', 1, 'Comprobación en tiempo de ejecución: dinámico.'],
            ],
          },
        ],
      },
      {
        ref: '3.2',
        title: 'Paradigmas de programación',
        items: 10,
        sets: [
          {
            options: ['Orientado a objetos', 'Funcional', 'Lógico'],
            ask: '¿Qué paradigma se describe?',
            cues: [
              ['Se modela con clases, herencia y objetos que encapsulan estado y comportamiento.', 0, 'Clases y objetos con estado: orientado a objetos.'],
              ['Las funciones son puras, los datos inmutables y se componen funciones de orden superior.', 1, 'Pureza e inmutabilidad: funcional.'],
              ['Se declaran hechos y reglas y el motor deduce las respuestas, como en Prolog.', 2, 'Hechos, reglas e inferencia: lógico.'],
              ['map, filter y reduce procesan la lista de ventas sin variables mutables.', 1, 'Funciones de orden superior sin mutación: funcional.'],
            ],
          },
          {
            options: ['Encapsulamiento', 'Herencia', 'Polimorfismo'],
            ask: '¿Qué principio de la POO se aplica?',
            cues: [
              ['El saldo es privado y solo cambia mediante depositar() y retirar().', 0, 'Estado oculto tras una interfaz: encapsulamiento.'],
              ['CuentaAhorro y CuentaCheques reutilizan los atributos y métodos de Cuenta.', 1, 'Subclases que reutilizan la superclase: herencia.'],
              ['Una lista de Figura llama a area() y cada subclase responde con su propio cálculo.', 2, 'Misma llamada, distinto comportamiento: polimorfismo.'],
              ['pagar() se comporta distinto para tarjeta, transferencia o efectivo.', 2, 'Un mensaje, varias implementaciones: polimorfismo.'],
            ],
          },
        ],
      },
      {
        ref: '3.3',
        title: 'Entornos de desarrollo',
        items: 10,
        sets: [
          {
            options: ['Integración continua', 'Entrega continua', 'Despliegue continuo'],
            ask: '¿Qué práctica se describe?',
            cues: [
              ['Cada push dispara la compilación y las pruebas automáticas en un servidor.', 0, 'Construir y probar cada cambio: integración continua.'],
              ['El artefacto queda siempre listo para producción, pero el pase lo aprueba una persona.', 1, 'Listo para liberar con aprobación manual: entrega continua.'],
              ['Todo cambio que pasa las pruebas llega a producción sin intervención humana.', 2, 'Sin aprobación manual: despliegue continuo.'],
              ['El equipo integra su código varias veces al día a la rama principal para detectar conflictos pronto.', 0, 'Integrar seguido y verificar: integración continua.'],
            ],
          },
          {
            options: ['Control de versiones (Git)', 'Gestor de dependencias (Maven)', 'Depurador'],
            ask: '¿Qué herramienta resuelve el problema?',
            cues: [
              ['Dos desarrolladores modificaron el mismo archivo y hay que ver quién cambió qué y cuándo.', 0, 'Historial y autoría de cambios: control de versiones.'],
              ['Cada máquina compila con versiones distintas de las mismas bibliotecas.', 1, 'Versiones declaradas y descargadas igual en todas partes: gestor de dependencias.'],
              ['Hay que detener el programa en una línea e inspeccionar el valor de las variables.', 2, 'Puntos de interrupción e inspección: depurador.'],
              ['Se quiere volver a la versión que funcionaba el viernes pasado.', 0, 'Regresar a un estado anterior: control de versiones.'],
            ],
          },
        ],
      },
      {
        ref: '3.4',
        title: 'Gestión de datos',
        items: 9,
        sets: [
          {
            options: ['Atomicidad', 'Aislamiento', 'Durabilidad'],
            ask: '¿Qué propiedad ACID se describe?',
            cues: [
              ['Si falla el cargo también se revierte el abono: la transferencia ocurre completa o no ocurre.', 0, 'Todo o nada: atomicidad.'],
              ['Dos cajeros que venden el último boleto al mismo tiempo no ven datos a medias del otro.', 1, 'Transacciones concurrentes sin interferencia: aislamiento.'],
              ['Una vez confirmada, la venta sobrevive a un corte de luz del servidor.', 2, 'Lo confirmado permanece: durabilidad.'],
              ['Un COMMIT exitoso queda grabado aunque el servidor se reinicie.', 2, 'Persistencia tras la confirmación: durabilidad.'],
            ],
          },
          {
            options: ['Índice', 'Vista', 'Disparador (trigger)'],
            ask: '¿Qué objeto de base de datos conviene?',
            cues: [
              ['La búsqueda por CURP recorre toda una tabla de cinco millones de filas.', 0, 'Evitar el recorrido completo: índice.'],
              ['Los analistas deben consultar ventas por región sin ver los datos personales de los clientes.', 1, 'Una consulta guardada que expone solo lo necesario: vista.'],
              ['Cada vez que se modifica un precio debe quedar registro automático en una bitácora.', 2, 'Acción automática ante un evento: disparador.'],
              ['Un reporte que filtra por fecha de venta tarda 40 segundos.', 0, 'Filtro frecuente sin índice: índice.'],
            ],
          },
        ],
      },
      {
        ref: '3.5',
        title: 'Plataformas de desarrollo',
        items: 10,
        sets: [
          {
            options: ['IaaS', 'PaaS', 'SaaS'],
            ask: '¿Qué modelo de servicio en la nube corresponde?',
            cues: [
              ['La empresa renta máquinas virtuales y administra el sistema operativo y sus parches.', 0, 'Infraestructura rentada, sistema operativo propio: IaaS.'],
              ['El equipo sube su código y el proveedor administra servidores, sistema operativo y escalamiento.', 1, 'Solo se entrega la aplicación: PaaS.'],
              ['La contadora usa un sistema de facturación en línea sin instalar ni administrar nada.', 2, 'Software listo para usar: SaaS.'],
              ['Hay que instalar un servicio de Windows de terceros en el servidor.', 0, 'Control del sistema operativo: IaaS.'],
            ],
          },
          {
            options: ['Aplicación nativa', 'Aplicación web progresiva (PWA)', 'Aplicación multiplataforma (Flutter)'],
            ask: '¿Qué tipo de aplicación conviene?',
            cues: [
              ['Se necesita el máximo desempeño y acceso completo al hardware de iOS.', 0, 'Máximo desempeño y hardware: nativa.'],
              ['Se quiere una app instalable desde el navegador, que funcione sin conexión y sin tiendas de apps.', 1, 'Instalable desde el navegador y offline: PWA.'],
              ['Un solo equipo debe publicar en Android e iOS desde una misma base de código compilada.', 2, 'Una base de código para dos plataformas: multiplataforma.'],
              ['El cliente no quiere pasar por la revisión de las tiendas y la app es un catálogo sencillo.', 1, 'Sin tiendas y sencilla: PWA.'],
            ],
          },
        ],
      },
    ],
  },
  {
    code: '4',
    title: 'Gestión de Proyectos de Software',
    subareas: [
      {
        ref: '4.1',
        title: 'Gestión de tiempos, costos, recursos humanos y de riesgo',
        items: 8,
        sets: [
          {
            options: ['Variación del costo (CV)', 'Índice de desempeño del costo (CPI)', 'Variación del cronograma (SV)'],
            ask: '¿Qué indicador del valor ganado conviene calcular?',
            cues: [
              ['El patrocinador pregunta cuánto valor se obtiene por cada peso gastado.', 1, 'Valor por peso: CPI = EV / AC.'],
              ['Se quiere saber, en pesos, cuánto se ha gastado de más respecto del valor ganado.', 0, 'Diferencia en pesos: CV = EV - AC.'],
              ['Se quiere saber si el valor ganado va adelante o atrás de lo planeado a la fecha.', 2, 'Ganado contra planeado: SV = EV - PV.'],
              ['Con EV = 80 y AC = 100 se necesita un índice que diga que se gana 0.8 por peso.', 1, '80 / 100 = 0.8: CPI.'],
            ],
          },
          {
            options: ['Evitar', 'Mitigar', 'Transferir'],
            ask: '¿Qué estrategia de respuesta al riesgo es?',
            cues: [
              ['Se contrata un seguro contra daños al centro de datos.', 2, 'Un tercero absorbe el impacto: transferir.'],
              ['Se elimina del alcance la integración con el sistema obsoleto que causaba el riesgo.', 0, 'Se quita la causa: evitar.'],
              ['Se capacita a un segundo desarrollador para reducir el impacto si se va el único experto.', 1, 'Se reduce probabilidad o impacto: mitigar.'],
              ['Se subcontrata a un proveedor con penalización contractual por retrasos.', 2, 'El contrato traslada la consecuencia: transferir.'],
            ],
          },
        ],
      },
      {
        ref: '4.2',
        title: 'Calidad de software',
        items: 10,
        sets: [
          {
            options: ['Verificación', 'Validación', 'Auditoría'],
            ask: '¿Qué actividad de calidad se describe?',
            cues: [
              ['Se revisa que el diseño cumpla lo especificado en la SRS: ¿construimos el producto correctamente?', 0, 'Contra la especificación: verificación.'],
              ['El cliente usa el sistema y confirma que resuelve su necesidad: ¿construimos el producto correcto?', 1, 'Contra la necesidad real: validación.'],
              ['Un equipo independiente revisa que el proyecto siga los procesos definidos por la organización.', 2, 'Cumplimiento de procesos por un tercero: auditoría.'],
              ['Una revisión por pares compara el código contra el documento de diseño.', 0, 'Producto contra especificación: verificación.'],
            ],
          },
          {
            options: ['Prueba unitaria', 'Prueba de integración', 'Prueba de aceptación'],
            ask: '¿Qué nivel de prueba es?',
            cues: [
              ['Se prueba el método calcularIVA() de forma aislada con valores límite.', 0, 'Una unidad aislada: unitaria.'],
              ['Se verifica que el módulo de pedidos y el de pagos intercambien datos correctamente.', 1, 'Interacción entre módulos: integración.'],
              ['El usuario final ejecuta los escenarios del contrato antes de firmar la liberación.', 2, 'El cliente decide si acepta: aceptación.'],
              ['Se simulan las dependencias con mocks para probar una sola clase.', 0, 'Una clase aislada: unitaria.'],
            ],
          },
        ],
      },
      {
        ref: '4.3',
        title: 'Metodologías de desarrollo',
        items: 12,
        sets: [
          {
            options: ['Cascada', 'Scrum', 'Kanban'],
            ask: '¿Qué metodología conviene?',
            cues: [
              ['Los requerimientos están congelados por contrato y cada fase se firma antes de la siguiente.', 0, 'Fases secuenciales con firma: cascada.'],
              ['El equipo entrega incrementos cada dos semanas con un product owner que prioriza el backlog.', 1, 'Sprints y product owner: Scrum.'],
              ['Un equipo de soporte atiende tickets continuos y quiere limitar el trabajo en curso sin iteraciones fijas.', 2, 'Flujo continuo con límite de WIP: Kanban.'],
              ['Se necesita retroalimentación frecuente del cliente sobre un producto cuyo alcance cambia.', 1, 'Iteraciones cortas con revisión: Scrum.'],
            ],
          },
          {
            options: ['Scrum Master', 'Product Owner', 'Equipo de desarrollo'],
            ask: '¿Qué rol de Scrum es responsable?',
            cues: [
              ['Ordenar el product backlog y maximizar el valor del producto.', 1, 'Dueño del backlog y del valor: Product Owner.'],
              ['Eliminar impedimentos y asegurar que se sigan las prácticas de Scrum.', 0, 'Facilitador del proceso: Scrum Master.'],
              ['Decidir cómo convertir el sprint backlog en un incremento terminado.', 2, 'El cómo es del equipo de desarrollo.'],
              ['Aceptar o rechazar los elementos terminados al final del sprint.', 1, 'Acepta el trabajo según su valor: Product Owner.'],
            ],
          },
        ],
      },
    ],
  },
];

export const ISOFT_TOTAL_ITEMS = ISOFT_AREAS.reduce(
  (n, a) => n + a.subareas.reduce((m, s) => m + s.items, 0),
  0,
);

/** A stable concept id per subárea, like the real importer's `n_<slug>_<hex>`. */
export function isoftNodeId(ref: string): string {
  return `n_isoft_${ref.replace('.', '_')}`;
}

const LETTERS = ['A', 'B', 'C'];

/**
 * The bank: per subárea, cues A0, B0, A1 are practice; A2, A3, B1, B2, B3 are sealed. The
 * stored option order is the option set's order - practice and mocks shuffle it per serve.
 */
export function isoftBank(): MockPractice[] {
  const out: MockPractice[] = [];
  for (const area of ISOFT_AREAS) {
    for (const sub of area.subareas) {
      const flat = sub.sets.flatMap((set, si) =>
        set.cues.map((cue, ci) => ({ set, cue, tag: `${'AB'[si]}${ci}` })),
      );
      for (const { set, cue, tag } of flat) {
        const sealed = !['A0', 'B0', 'A1'].includes(tag);
        const id = `pq_isoft_${sub.ref.replace('.', '_')}_${tag.toLowerCase()}`;
        out.push({
          item_id: id,
          item_version_id: `${id}_v1`,
          node_id: isoftNodeId(sub.ref),
          node_title: sub.title,
          stem: `${cue[0]} ${set.ask}`,
          options: set.options.map((text, i) => ({ key: LETTERS[i], text })),
          answer: LETTERS[cue[1]],
          explanation: cue[2],
          check: 'checked',
          pool: sealed ? 'mock' : 'practice',
          ref: sub.ref,
        });
      }
    }
  }
  return out;
}

export const ISOFT_CARDS: MockCard[] = [
  {
    item_id: 'c_isoft_cpi',
    node_id: isoftNodeId('4.1'),
    node_title: 'Gestión de tiempos, costos, recursos humanos y de riesgo',
    front: '**CPI** (índice de desempeño del costo)',
    back: '$CPI = EV / AC$. Menor que 1: se gasta más de lo que se gana.',
    source: 'notas/evm.md',
    due_in_hours: -3,
  },
  {
    item_id: 'c_isoft_spi',
    node_id: isoftNodeId('4.1'),
    node_title: 'Gestión de tiempos, costos, recursos humanos y de riesgo',
    front: '**SPI** (índice de desempeño del cronograma)',
    back: '$SPI = EV / PV$. Menor que 1: vas atrasado.',
    source: 'notas/evm.md',
    due_in_hours: 30,
  },
  {
    item_id: 'c_isoft_include',
    node_id: isoftNodeId('1.3'),
    node_title: 'Técnicas y herramientas de documentación',
    front: '`<<include>>` contra `<<extend>>`',
    back: 'Include: comportamiento obligatorio que el caso base siempre ejecuta. Extend: opcional o condicional.',
    source: 'notas/uml.md',
    due_in_hours: -20,
  },
  {
    item_id: 'c_isoft_acid',
    node_id: isoftNodeId('3.4'),
    node_title: 'Gestión de datos',
    front: 'ACID',
    back: 'Atomicidad, Consistencia, Aislamiento, Durabilidad.',
    source: 'notas/bd.md',
  },
  {
    item_id: 'c_isoft_moscow',
    node_id: isoftNodeId('1.2'),
    node_title: 'Técnicas y herramientas para la obtención, análisis, priorización y validación',
    front: '**MoSCoW**',
    back: "Must, Should, Could, Won't (this time): prioriza el alcance de una entrega con fecha fija.",
    source: 'notas/requerimientos.md',
  },
  {
    item_id: 'c_isoft_25010',
    node_id: isoftNodeId('1.1'),
    node_title: 'Tipos de requerimientos',
    front: 'ISO/IEC 25010: las ocho características de calidad del producto',
    back: 'Adecuación funcional, eficiencia de desempeño, compatibilidad, usabilidad, confiabilidad, seguridad, mantenibilidad, portabilidad.',
    source: 'notas/requerimientos.md',
    due_in_hours: 50,
  },
];

export const ISOFT_TABLES: MockTable[] = [
  {
    table_id: 't_isoft_evm',
    title: 'Valor ganado (EVM)',
    node_id: isoftNodeId('4.1'),
    node_title: 'Gestión de tiempos, costos, recursos humanos y de riesgo',
    columns: ['Métrica', 'Fórmula', 'Lectura'],
    rows: [
      ['CV', 'EV - AC', 'negativo: sobrecosto'],
      ['SV', 'EV - PV', 'negativo: atraso'],
      ['CPI', 'EV / AC', 'menor que 1: sobrecosto'],
      ['SPI', 'EV / PV', 'menor que 1: atraso'],
    ],
    source: 'notas/evm.md',
    author: 'import',
    created_at: '2026-09-20T10:00:00Z',
  },
];
