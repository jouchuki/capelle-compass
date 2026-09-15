// PDF Export utility for Compass municipal dashboard
// Uses html2canvas + jsPDF for client-side PDF generation

let jsPDF = null;
let html2canvas = null;

async function loadLibraries() {
  if (jsPDF && html2canvas) return;
  const [jsPDFModule, h2cModule] = await Promise.all([
    import('jspdf'),
    import('html2canvas'),
  ]);
  jsPDF = jsPDFModule.jsPDF || jsPDFModule.default;
  html2canvas = h2cModule.default || h2cModule;
}

/**
 * Export a page element to PDF with Capelle branding
 * @param {string} pageId - The page container id (without 'page-' prefix)
 * @param {string} title - The document title
 */
export async function exportPageToPDF(pageId, title) {
  await loadLibraries();

  const pageEl = document.getElementById(`page-${pageId}`);
  if (!pageEl) {
    console.error('Page element not found:', pageId);
    return;
  }

  // Show loading indicator
  const btn = document.querySelector(`.export-pdf-btn[data-page="${pageId}"]`);
  const originalText = btn ? btn.textContent : '';
  if (btn) {
    btn.textContent = 'Exporteren...';
    btn.disabled = true;
  }

  try {
    // Temporarily make page fully visible for capture
    pageEl.style.overflow = 'visible';
    pageEl.style.maxHeight = 'none';

    const canvas = await html2canvas(pageEl, {
      scale: 2,
      useCORS: true,
      backgroundColor: '#f4f4f6',
      logging: false,
      windowWidth: 1400,
    });

    const imgData = canvas.toDataURL('image/png');
    const imgWidth = 190; // A4 width minus margins (mm)
    const pageHeight = 277; // A4 height minus margins (mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width;

    const pdf = new jsPDF('p', 'mm', 'a4');

    // Header
    pdf.setFillColor(21, 66, 115);
    pdf.rect(0, 0, 210, 20, 'F');
    pdf.setTextColor(255, 255, 255);
    pdf.setFontSize(14);
    pdf.setFont('helvetica', 'bold');
    pdf.text('Compass municipal dashboard', 10, 13);
    pdf.setFontSize(9);
    pdf.setFont('helvetica', 'normal');
    pdf.text('Kwaliteit van Leven Platform — Gemeente Capelle aan den IJssel', 10, 18);

    // Date
    const now = new Date();
    const dateStr = now.toLocaleDateString('nl-NL', { year: 'numeric', month: 'long', day: 'numeric' });
    pdf.setTextColor(200, 200, 200);
    pdf.setFontSize(8);
    pdf.text(dateStr, 200, 13, { align: 'right' });

    // Title bar
    pdf.setFillColor(231, 241, 249);
    pdf.rect(0, 20, 210, 10, 'F');
    pdf.setTextColor(21, 66, 115);
    pdf.setFontSize(11);
    pdf.setFont('helvetica', 'bold');
    pdf.text(title, 10, 27);

    // Content - split across pages if needed
    let yOffset = 32;
    let heightLeft = imgHeight;

    // First page
    const firstPageHeight = Math.min(heightLeft, pageHeight - yOffset);
    pdf.addImage(imgData, 'PNG', 10, yOffset, imgWidth, imgHeight);
    heightLeft -= (pageHeight - yOffset);

    // Additional pages
    while (heightLeft > 0) {
      pdf.addPage();
      yOffset = -(pageHeight - 32 - (imgHeight - heightLeft));
      pdf.addImage(imgData, 'PNG', 10, -(imgHeight - heightLeft) + 10, imgWidth, imgHeight);
      heightLeft -= pageHeight;
    }

    // Footer on last page
    const pageCount = pdf.internal.getNumberOfPages();
    for (let i = 1; i <= pageCount; i++) {
      pdf.setPage(i);
      pdf.setFontSize(7);
      pdf.setTextColor(150, 150, 150);
      pdf.setFont('helvetica', 'normal');
      pdf.text(`Bron: CBS Open Data / Compass municipal dashboard | Gegenereerd: ${dateStr} | Pagina ${i}/${pageCount}`, 10, 290);
      pdf.text('Dit document bevat mogelijk AI-gegenereerde inzichten. Raadpleeg originele bronnen voor beleidsbeslissingen.', 10, 294);
    }

    // Save
    const filename = `leefkompas-${pageId}-${now.toISOString().split('T')[0]}.pdf`;
    pdf.save(filename);
  } catch (error) {
    console.error('PDF export failed:', error);
    alert('PDF export mislukt. Probeer het opnieuw.');
  } finally {
    if (btn) {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  }
}

/**
 * Create a PDF export button HTML string
 */
export function pdfExportButton(pageId) {
  return `<button class="export-pdf-btn" data-page="${pageId}" title="Exporteer als PDF voor raadsstukken">PDF Export</button>`;
}
