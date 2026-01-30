"""
Invoice Generator

Creates professional PDF invoices for crypto payments.
Uses ReportLab for PDF generation.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

logger = logging.getLogger(__name__)


class InvoiceGenerator:
    """
    Generates professional PDF invoices for crypto payments.

    Features:
    - Clean, modern design
    - Multi-currency support (crypto + fiat equivalent)
    - Customizable sender profile
    - Auto-generated invoice numbers
    """

    def __init__(self, output_dir: str | Path):
        """Initialize with output directory for generated PDFs."""
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_invoice(
        self,
        # Invoice details
        invoice_number: str,
        invoice_date: datetime,
        # Sender info
        sender_name: str,
        sender_email: str,
        sender_wallet: str,
        # Recipient info
        recipient_name: str,
        recipient_email: str,
        # Payment details
        crypto_amount: float,
        token_type: str,
        # Optional fields
        sender_business: str = "",
        sender_address: str = "",
        sender_tax_id: str = "",
        recipient_wallet: Optional[str] = None,
        usd_value: Optional[float] = None,
        cad_value: Optional[float] = None,
        work_description: str = "",
        notes: str = "",
    ) -> tuple[bytes, str]:
        """
        Generate a PDF invoice.

        Returns:
            Tuple of (pdf_bytes, filename)
        """
        # Generate filename
        safe_number = invoice_number.replace("/", "-").replace("\\", "-")
        filename = f"invoice_{safe_number}_{invoice_date.strftime('%Y%m%d')}.pdf"
        filepath = self.output_dir / filename

        # Create PDF document
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        # Styles
        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "InvoiceTitle",
            parent=styles["Heading1"],
            fontSize=28,
            textColor=colors.HexColor("#1a1a2e"),
            spaceAfter=6,
        )

        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            fontSize=12,
            textColor=colors.HexColor("#4a4a6a"),
            spaceBefore=12,
            spaceAfter=6,
        )

        normal_style = ParagraphStyle(
            "NormalText",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#333333"),
            leading=14,
        )

        small_style = ParagraphStyle(
            "SmallText",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#666666"),
            leading=12,
        )

        # Build document elements
        elements = []

        # Header: INVOICE title and number
        header_data = [
            [
                Paragraph("INVOICE", title_style),
                Paragraph(f"<b>{invoice_number}</b>", ParagraphStyle(
                    "InvoiceNumber",
                    parent=styles["Normal"],
                    fontSize=14,
                    alignment=TA_RIGHT,
                    textColor=colors.HexColor("#4a4a6a"),
                )),
            ],
            [
                "",
                Paragraph(f"Date: {invoice_date.strftime('%B %d, %Y')}", ParagraphStyle(
                    "InvoiceDate",
                    parent=styles["Normal"],
                    fontSize=10,
                    alignment=TA_RIGHT,
                    textColor=colors.HexColor("#666666"),
                )),
            ],
        ]

        header_table = Table(header_data, colWidths=[4 * inch, 3 * inch])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 0.3 * inch))

        # Horizontal line
        elements.append(HRFlowable(
            width="100%",
            thickness=1,
            color=colors.HexColor("#e0e0e0"),
            spaceAfter=0.3 * inch,
        ))

        # From/To section
        from_lines = [f"<b>{sender_name}</b>"]
        if sender_business:
            from_lines.append(sender_business)
        if sender_address:
            from_lines.append(sender_address)
        from_lines.append(sender_email)
        if sender_tax_id:
            from_lines.append(f"Tax ID: {sender_tax_id}")

        to_lines = [f"<b>{recipient_name}</b>"]
        to_lines.append(recipient_email)
        if recipient_wallet:
            to_lines.append(f"Wallet: {recipient_wallet[:20]}...")

        from_to_data = [
            [
                Paragraph("FROM", heading_style),
                Paragraph("BILL TO", heading_style),
            ],
            [
                Paragraph("<br/>".join(from_lines), normal_style),
                Paragraph("<br/>".join(to_lines), normal_style),
            ],
        ]

        from_to_table = Table(from_to_data, colWidths=[3.5 * inch, 3.5 * inch])
        from_to_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(from_to_table)
        elements.append(Spacer(1, 0.4 * inch))

        # Work Description
        if work_description:
            elements.append(Paragraph("DESCRIPTION", heading_style))
            elements.append(Paragraph(work_description, normal_style))
            elements.append(Spacer(1, 0.3 * inch))

        # Amount section
        elements.append(HRFlowable(
            width="100%",
            thickness=1,
            color=colors.HexColor("#e0e0e0"),
            spaceBefore=0.2 * inch,
            spaceAfter=0.3 * inch,
        ))

        # Amount table
        amount_data = [
            ["Amount Due:", f"{crypto_amount} {token_type.upper()}"],
        ]

        if usd_value:
            amount_data.append(["USD Equivalent:", f"${usd_value:,.2f} USD"])

        if cad_value:
            amount_data.append(["CAD Equivalent:", f"${cad_value:,.2f} CAD"])

        amount_table = Table(
            amount_data,
            colWidths=[4.5 * inch, 2.5 * inch],
        )
        amount_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 12),
            ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (1, 0), (1, 0), 16),
            ("TEXTCOLOR", (1, 0), (1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(amount_table)
        elements.append(Spacer(1, 0.4 * inch))

        # Payment section
        elements.append(HRFlowable(
            width="100%",
            thickness=1,
            color=colors.HexColor("#e0e0e0"),
            spaceAfter=0.3 * inch,
        ))

        elements.append(Paragraph("PAYMENT DETAILS", heading_style))

        wallet_text = f"""
        <b>Send {token_type.upper()} to:</b><br/>
        <font face="Courier" size="9">{sender_wallet}</font>
        """
        elements.append(Paragraph(wallet_text, normal_style))
        elements.append(Spacer(1, 0.2 * inch))

        # Notes
        if notes:
            elements.append(Spacer(1, 0.3 * inch))
            elements.append(HRFlowable(
                width="100%",
                thickness=1,
                color=colors.HexColor("#e0e0e0"),
                spaceAfter=0.2 * inch,
            ))
            elements.append(Paragraph("NOTES", heading_style))
            elements.append(Paragraph(notes, small_style))

        # Footer
        elements.append(Spacer(1, 0.5 * inch))
        elements.append(Paragraph(
            "Thank you for your business!",
            ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=10,
                textColor=colors.HexColor("#888888"),
                alignment=TA_CENTER,
            ),
        ))

        # Build PDF
        doc.build(elements)

        # Get bytes
        pdf_bytes = buffer.getvalue()

        # Also save to file
        with open(filepath, "wb") as f:
            f.write(pdf_bytes)

        logger.info(f"Generated invoice: {filename}")

        return pdf_bytes, filename


def generate_invoice_preview(
    invoice_number: str,
    sender_name: str,
    recipient_name: str,
    crypto_amount: float,
    token_type: str,
    usd_value: Optional[float] = None,
) -> str:
    """
    Generate a simple text preview of an invoice.

    Useful for quick display in the UI.
    """
    preview = f"""
Invoice #{invoice_number}
From: {sender_name}
To: {recipient_name}
Amount: {crypto_amount} {token_type}
"""
    if usd_value:
        preview += f"USD Value: ${usd_value:,.2f}\n"

    return preview.strip()
