#  API REST - Sistema de Control de Producción e Inventario

> **Transformando datos operativos en decisiones estratégicas.**

Este proyecto es el *backend* de una aplicación diseñada para optimizar la gestión operativa en empresas de impresión de gran formato. Su objetivo principal es automatizar el control de inventarios, rastrear las órdenes de producción y proporcionar información estructurada para el análisis financiero y la mejora continua.

##  Impacto en el Negocio (Visión de Ingeniería Administrativa)

A diferencia de un simple registro de datos, esta arquitectura está pensada para resolver problemas reales de las operaciones y las finanzas:
* **Integridad del Inventario:** Descuento automático de materia prima en tiempo real, evitando quiebres de stock.
* **Trazabilidad de Costos:** Seguimiento detallado de los insumos consumidos por cada orden de producción para calcular márgenes de rentabilidad más precisos.
* **Toma de Decisiones:** Estructuración de bases de datos relacionales orientadas a la fácil extracción de métricas para herramientas de Business Intelligence (como Power BI o Looker Studio).

##  Stack Tecnológico

La solución está construida bajo una arquitectura robusta, escalable y eficiente:
* **Lenguaje:** Python 3.17
* **Framework Web:** FastAPI (Alta velocidad y generación automática de documentación Swagger/OpenAPI).
* **ORM:** SQLAlchemy (Manejo eficiente de transacciones y consultas complejas).
* **Base de Datos:** MySQL (Estructura relacional para mantener la integridad de la información operativa).

##  Características Principales (Endpoints)

* **Módulo de Inventario:** Creación, lectura, actualización y eliminación (CRUD) de insumos y materiales.
* **Módulo de Producción:** Registro de nuevas órdenes, asignación de operarios y cambio de estados (Pendiente, En Proceso, Finalizado).
* **Gestión de Concurrencia:** Implementación de bloqueos a nivel de fila (*row-level locking*) en transacciones de bases de datos para asegurar cálculos exactos cuando múltiples operarios registran producción simultáneamente.

##  Planificación y Gestión del Proyecto

El desarrollo de esta aplicación se gestiona de forma estructurada para asegurar la entrega de valor continuo. 

* **Tablero Kanban:** Puedes ver el progreso en tiempo real de las tareas, nuevas características y corrección de errores en la sección de [Projects de este repositorio](#) *(Nota: Aquí debes poner el link de tu pestaña Projects).*
* **Resolución de Problemas:** Utilizo la sección de **Issues** para documentar requerimientos operativos, traducir esos requerimientos a tareas técnicas y hacer control de calidad.

## 👨‍💻 Autor

**David Felipe Espinosa Goez**
Estudiante de Ingeniería Administrativa apasionado por el análisis de datos, las finanzas y el desarrollo *backend*. 
* [Mi perfil de LinkedIn](#) *https://www.linkedin.com/in/david-felipe-espinosa-goez-21b5843b6/*
