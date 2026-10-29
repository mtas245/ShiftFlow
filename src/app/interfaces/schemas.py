from pydantic import BaseModel, ConfigDict, Field, StrictStr


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: StrictStr = Field(max_length=254)
    password: StrictStr = Field(max_length=1024, repr=False)


class LoginResponse(BaseModel):
    csrf_token: str


class AccountRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: StrictStr = Field(max_length=254)
    password: StrictStr = Field(max_length=1024, repr=False)


class EmailAddressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: StrictStr = Field(max_length=254)


class AccountActionTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: StrictStr = Field(max_length=128, repr=False)


class PasswordResetConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: StrictStr = Field(max_length=128, repr=False)
    new_password: StrictStr = Field(max_length=1024, repr=False)


class AccountLifecycleMessageResponse(BaseModel):
    message: str = "If eligible, an email will be sent."


class CompanyRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_name: str
    business_email: str
    business_phone: str
    business_address: str
    industry: str
    display_name: StrictStr


class CompanyRegistrationResponse(BaseModel):
    company_id: str
    joining_code: str
    role: str


class CompanyJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    joining_code: StrictStr
    display_name: StrictStr


class CompanyJoinResponse(BaseModel):
    company_id: str
    role: str


class CompanyMembershipResponse(BaseModel):
    membership_id: str
    company_id: str
    name: str
    role: str


class CompanyMembershipListResponse(BaseModel):
    items: list[CompanyMembershipResponse]
    next_cursor: str | None


class EmployeeMembershipResponse(BaseModel):
    membership_id: str
    display_name: str


class EmployeeRosterResponse(BaseModel):
    items: list[EmployeeMembershipResponse]
    next_cursor: str | None


class AvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: StrictStr
    end_at: StrictStr


class AvailabilityResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    start_at: str
    end_at: str


class AvailabilityListResponse(BaseModel):
    items: list[AvailabilityResponse]
    next_cursor: str | None


class ShiftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: StrictStr
    end_at: StrictStr


class ShiftAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    membership_id: StrictStr


class ShiftAssignmentResponse(BaseModel):
    id: str
    shift_id: str
    company_id: str
    membership_id: str
    display_name: str | None


class ShiftResponse(BaseModel):
    id: str
    company_id: str
    start_at: str
    end_at: str
    assignment: ShiftAssignmentResponse | None


class ShiftListResponse(BaseModel):
    items: list[ShiftResponse]
    next_cursor: str | None


class ErrorDetail(BaseModel):
    code: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
