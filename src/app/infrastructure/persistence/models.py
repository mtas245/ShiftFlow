from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CompanyRecord(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("joining_code", name="uq_companies_joining_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    business_email: Mapped[str] = mapped_column(Text, nullable=False)
    business_phone: Mapped[str] = mapped_column(String(16), nullable=False)
    business_address: Mapped[str] = mapped_column(Text, nullable=False)
    industry: Mapped[str] = mapped_column(Text, nullable=False)
    joining_code: Mapped[str] = mapped_column(String(8), nullable=False)


class CompanyMembershipRecord(Base):
    __tablename__ = "company_memberships"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "user_id",
            name="uq_company_memberships_company_user",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
