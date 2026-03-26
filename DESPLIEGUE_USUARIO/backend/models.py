# backend/models.py
from datetime import datetime, date
from sqlalchemy import (
    String, Integer, Date, DateTime, Text, ForeignKey, Numeric, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

# ---- Catálogos de materiales ----
class Material(Base):
    __tablename__ = "materiales"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True, index=True)

class Marca(Base):
    __tablename__ = "marcas"
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiales.id"))
    nombre: Mapped[str] = mapped_column(String(120))
    material: Mapped[Material] = relationship(backref="marcas")

class MedidaMaterial(Base):
    __tablename__ = "medidas_material"
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiales.id"))
    ancho_cm: Mapped[float] = mapped_column(Numeric(10, 2), index=True)
    material: Mapped[Material] = relationship(backref="medidas")

# ---- Catálogos de laminados ----
class LamTipo(Base):
    __tablename__ = "lam_tipos"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True)

class LamMarca(Base):
    __tablename__ = "lam_marcas"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True)

class LamMedida(Base):
    __tablename__ = "lam_medidas"
    id: Mapped[int] = mapped_column(primary_key=True)
    ancho_cm: Mapped[float] = mapped_column(Numeric(10, 2), unique=True, index=True)

# ---- Inventario ----
class InventarioMaterial(Base):
    __tablename__ = "inventario_material"
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiales.id"))
    marca_id: Mapped[int | None] = mapped_column(ForeignKey("marcas.id"), nullable=True)
    ancho_cm: Mapped[float] = mapped_column(Numeric(10, 2), index=True)
    stock_cm: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

class InventarioLaminado(Base):
    __tablename__ = "inventario_laminado"
    id: Mapped[int] = mapped_column(primary_key=True)
    lam_tipo_id: Mapped[int] = mapped_column(ForeignKey("lam_tipos.id"))
    lam_marca_id: Mapped[int | None] = mapped_column(ForeignKey("lam_marcas.id"), nullable=True)
    ancho_cm: Mapped[float] = mapped_column(Numeric(10, 2), index=True)
    stock_cm: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

# ---- Órdenes ----
class Orden(Base):
    __tablename__ = "ordenes"
    id: Mapped[int] = mapped_column(primary_key=True)
    estado: Mapped[str] = mapped_column(String(20), default="PENDIENTE", index=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    fecha_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    consecutivo: Mapped[str | None] = mapped_column(String(70), nullable=True, index=True)

    material_id: Mapped[int] = mapped_column(ForeignKey("materiales.id"))
    marca_id: Mapped[int | None] = mapped_column(ForeignKey("marcas.id"), nullable=True)
    ancho_orden_cm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    ancho_rollo_cm: Mapped[float] = mapped_column(Numeric(10, 2))

    largo_cm: Mapped[float] = mapped_column(Numeric(12, 2))
    repeticiones: Mapped[int] = mapped_column(Integer)
    espacio_reps_cm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    ruta: Mapped[str] = mapped_column(String(255), default="")
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)

    # laminado
    lam_tipo_id: Mapped[int | None] = mapped_column(ForeignKey("lam_tipos.id"), nullable=True)
    lam_marca_id: Mapped[int | None] = mapped_column(ForeignKey("lam_marcas.id"), nullable=True)
    lam_ancho_cm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    # métricas de producción
    impreso_cm: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    desp_largo_cm: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    desp_ancho_cm: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    lam_consumo_cm: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    merma_cancel_cm: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)

# ---- Movimientos de inventario ----
class MovimientoInv(Base):
    __tablename__ = "movimientos_inv"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=func.now(), index=True)
    tipo: Mapped[str] = mapped_column(String(20))  # ENTRADA/SALIDA/AJUSTE/MERMA
    tabla: Mapped[str] = mapped_column(String(20))  # material/laminado
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materiales.id"), nullable=True)
    marca_id: Mapped[int | None] = mapped_column(ForeignKey("marcas.id"), nullable=True)
    lam_tipo_id: Mapped[int | None] = mapped_column(ForeignKey("lam_tipos.id"), nullable=True)
    lam_marca_id: Mapped[int | None] = mapped_column(ForeignKey("lam_marcas.id"), nullable=True)
    ancho_cm: Mapped[float] = mapped_column(Numeric(10, 2))
    cantidad_cm: Mapped[float] = mapped_column(Numeric(14, 2))
    id_orden: Mapped[int | None] = mapped_column(ForeignKey("ordenes.id"), nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
