/**
 * Sets the background color of rows based on a hex code in a column
 * named 'color' (case-insensitive). Handles 6 or 8-digit hex codes
 * and adds the '#' prefix if it is missing.
 */
function colorRowsBasedOnHex() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const dataRange = sheet.getDataRange();
  const values = dataRange.getValues();
  const header = values[0];
  
  const lowerCaseHeader = header.map(h => typeof h === 'string' ? h.toLowerCase() : '');
  const colorColIndex = lowerCaseHeader.indexOf("color");
  
  if (colorColIndex === -1) {
    SpreadsheetApp.getUi().alert('A column named "color" could not be found.');
    return;
  }

  for (let i = 1; i < values.length; i++) {
    let hexColor = values[i][colorColIndex];
    
    // Check if the cell contains a valid 6 or 8-digit hex string.
    if (typeof hexColor === 'string' && /^[0-9A-F]{6,8}$/i.test(hexColor)) {
      // Add the required '#' prefix if it's missing.
      if (hexColor.charAt(0) !== '#') {
        hexColor = '#' + hexColor;
      }
      
      // Google Sheets only supports 6-digit hex, so truncate 8-digit codes.
      // The first two digits in an 8-digit hex code are for alpha (transparency),
      // which Sheets sets separately. We will use the 6 color digits.
      if (hexColor.length === 9) { // # + 8 digits
          hexColor = '#' + hexColor.substring(3);
      }

      const rowRange = sheet.getRange(i + 1, 1, 1, sheet.getLastColumn());
      rowRange.setBackground(hexColor);
    }
  }
}

/**
 * Adds a custom menu to the spreadsheet to run the script easily.
 */
function onOpen() {
  SpreadsheetApp.getUi()
      .createMenu('Custom Tools')
      .addItem('Apply Row Colors', 'colorRowsBasedOnHex')
      .addToUi();
}