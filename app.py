from flask import Flask, request, render_template, send_file, redirect, url_for
import pandas as pd
from datetime import datetime, time, timedelta
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import uuid

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'  # Folder to store uploaded files
app.config['DOWNLOAD_FOLDER'] = 'downloads'  # Folder to store downloadable files

# Ensure the upload and download folders exist
for folder in [app.config['UPLOAD_FOLDER'], app.config['DOWNLOAD_FOLDER']]:
    if not os.path.exists(folder):
        os.makedirs(folder)

@app.template_filter()
def intcomma(value):
    return "{:,.2f}".format(value)

# Register the filter
app.jinja_env.filters['intcomma'] = intcomma

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return render_template('error.html', message='No file part in the request.'), 400

    file = request.files['file']

    if file.filename == '':
        return render_template('error.html', message='No selected file.'), 400

    if file:
        # Save the file to the upload folder
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)

        # Process the file
        try:
            df, totals, plot_files, monthly_analysis, months_included = process_file(filepath)

            totals_display = [
                {'Metric': 'Total Tip', 'Value': totals['total_tip']},
                {'Metric': 'Completion', 'Value': totals['total_completion']},
                {'Metric': 'Morning Extra Pay', 'Value': totals['total_extra_pay']},
                {'Metric': 'Morning Hours Worked', 'Value': totals['total_morning_hours']},
                {'Metric': 'Hours Worked', 'Value': totals['total_hours_worked']},
                {'Metric': 'Average Hourly Salary', 'Value': totals['average_hourly_salary']},
            ]

            # Save the results to a CSV file for download
            download_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], 'salary_results.csv')
            pd.DataFrame(totals_display).to_csv(download_filepath, index=False)

            # Pass the monthly analysis to the template
            return render_template('results.html', totals=totals_display, plot_files=plot_files, monthly_analysis=monthly_analysis, months_included=months_included)
        except Exception as e:
            return render_template('error.html', message=str(e)), 400
        finally:
            # Remove the file after processing
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
    else:
        return render_template('error.html', message='Invalid file type'), 400

@app.route('/download', methods=['GET', 'POST'])
def download_file():
    if request.method == 'POST':
        # Handle the selected months
        selected_months = request.form.getlist('months')
        if not selected_months:
            return render_template('error.html', message='No months selected for download.'), 400

        # Load the processed data (You might need to adjust this part based on how you store the processed data)
        data_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], 'processed_data.csv')
        if not os.path.exists(data_filepath):
            return render_template('error.html', message='No processed data available.'), 400

        df = pd.read_csv(data_filepath)

        # Filter the data for the selected months
        df['month_year'] = pd.to_datetime(df['תאריך']).dt.to_period('M').astype(str)
        filtered_df = df[df['month_year'].isin(selected_months)]

        # Save the filtered data to a CSV file
        download_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], 'salary_results_filtered.csv')
        filtered_df.to_csv(download_filepath, index=False)

        return send_file(
            download_filepath,
            mimetype='text/csv',
            as_attachment=True,
        )
    else:
        # Default behavior
        download_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], 'salary_results.csv')
        if os.path.exists(download_filepath):
            return send_file(
                download_filepath,
                mimetype='text/csv',
                as_attachment=True,
            )
        else:
            return render_template('error.html', message='No results available for download.'), 400

def read_and_clean_excel(file_path):
    """
    Reads the Excel file and performs initial cleaning.
    """
    # Read the Excel file without skipping rows
    df = pd.read_excel(file_path, header=None)

    # Find the header row index (row containing 'עובד' in the first column)
    header_row_index = df[df.iloc[:, 0] == 'עובד'].index
    if len(header_row_index) == 0:
        available_columns = ', '.join([str(col) for col in df.columns])
        raise ValueError(f"Header row with 'עובד' not found. Available columns: {available_columns}")
    else:
        header_row_index = header_row_index[0]

    # Set the header
    df.columns = df.iloc[header_row_index]

    # Drop all rows up to and including the header row
    df = df[(header_row_index + 1):].reset_index(drop=True)

    # Remove rows where 'עובד' is NaN or equals 'עובד' or 'סיכום'
    df = df[df['עובד'].notna()]
    df = df[~df['עובד'].isin(['עובד', 'סיכום'])]

    # Reset index after filtering
    df = df.reset_index(drop=True)

    return df


def process_file(file_path):
    """
    Reads, cleans, and processes the Excel file.
    """
    plot_files = None

    # Read and clean the Excel file
    df = read_and_clean_excel(file_path)

    # Preprocess data
    df = preprocess_data(df)

    # Check if 'כניסה' and 'יציאה' columns exist
    if 'כניסה' in df.columns and 'יציאה' in df.columns:
        # Apply the extra pay calculation
        df['extra_pay'], df['morning_hours'] = zip(*df.apply(calculate_extra_pay, axis=1))
    else:
        # If shift times are not available, set extra pay and morning hours to zero
        df['extra_pay'] = 0.0
        df['morning_hours'] = 0.0
        print("Shift start and end times are not available. Extra pay cannot be calculated.")

    # Calculate hours worked and total salary
    df['hours_worked'] = df.apply(calculate_shift_duration, axis=1)
    df['total_salary'] = df['טיפ מזומן'] + df['השלמה'] + df['extra_pay']

    # Save the processed data to a CSV file for later use
    data_filepath = os.path.join(app.config['DOWNLOAD_FOLDER'], 'processed_data.csv')
    df.to_csv(data_filepath, index=False)

    # Calculate totals
    totals = calculate_totals(df)

    # Add 'month_year' column
    df['month_year'] = df['תאריך'].dt.to_period('M')

    # Get the list of months included
    months_included = df['month_year'].dt.strftime('%B %Y').unique().tolist()

    # Check for multiple months
    unique_months = df['month_year'].nunique()

    monthly_analysis = None

    if unique_months > 1:
        grouped, plot_files = generate_trends(df)
        monthly_analysis = grouped.to_dict('records')
    else:
        # Generate monthly analysis even if only one month
        grouped, plot_files = generate_trends(df)
        monthly_analysis = grouped.to_dict('records')

    return df, totals, plot_files, monthly_analysis, months_included

def preprocess_data(df):
    """
    Performs data type conversions and cleans specific columns.
    """
    # Detect Shabbat shifts (rows where 'תאריך' contains the ✡️ symbol)
    df['is_shabbat'] = df['תאריך'].astype(str).str.contains('✡️')

    # Replace the ✡️ symbol with a placeholder before cleaning
    df['תאריך'] = df['תאריך'].astype(str).str.replace('✡️', 'SHABBAT', regex=False)

    # Clean the 'תאריך' column to remove special characters except 'SHABBAT'
    df['תאריך'] = df['תאריך'].str.replace(r'[^0-9/SHABBAT]', '', regex=True)

    # Remove the 'SHABBAT' placeholder
    df['תאריך'] = df['תאריך'].str.replace('SHABBAT', '', regex=False)

    # Convert 'תאריך' to datetime
    df['תאריך'] = pd.to_datetime(df['תאריך'], errors='coerce', dayfirst=True)

    # Remove rows with NaN dates
    df = df[df['תאריך'].notna()]

    # Remove rows where 'עובד' or 'תאריך' contain 'סיכום' or other non-data labels
    df = df[~df['עובד'].astype(str).str.contains('סיכום|Total|Summary', na=False)]
    df = df[~df['תאריך'].astype(str).str.contains('סיכום|Total|Summary', na=False)]

    # Clean all numerical columns by removing commas and non-numeric characters
    numerical_cols = df.select_dtypes(include=['object', 'float', 'int']).columns.tolist()
    # Exclude 'כניסה' and 'יציאה' from numerical columns
    numerical_cols = [col for col in numerical_cols if col not in ['כניסה', 'יציאה', 'עובד', 'תאריך', 'is_shabbat', 'month_year']]

    for col in numerical_cols:
        # Remove commas
        df[col] = df[col].astype(str).str.replace(',', '')
        # Remove any non-numeric characters except decimal points and negative signs
        df[col] = df[col].str.replace(r'[^0-9\.-]', '', regex=True)
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df

def calculate_extra_pay(row):
    """
    Calculates extra pay and morning hours worked.
    """
    # Common date for all datetime objects
    common_date = datetime(1900, 1, 1).date()

    # Fixed hours for extra pay
    fixed_start = datetime.combine(common_date, time(6, 0))    # 6:00 AM
    fixed_end = datetime.combine(common_date, time(12, 15))    # 12:15 PM

    # Get shift start and end times
    shift_start_str = row.get('כניסה')
    shift_end_str = row.get('יציאה')

    # Return 0 if times are missing
    if pd.isna(shift_start_str) or pd.isna(shift_end_str):
        return 0.0, 0.0

    # Parse shift times
    try:
        shift_start_time = datetime.strptime(shift_start_str, '%H:%M').time()
        shift_end_time = datetime.strptime(shift_end_str, '%H:%M').time()
    except ValueError:
        # If time format is incorrect, set extra pay and morning hours to 0
        return 0.0, 0.0

    # Combine times with the common date
    shift_start = datetime.combine(common_date, shift_start_time)
    shift_end = datetime.combine(common_date, shift_end_time)

    # Handle overnight shifts
    if shift_end < shift_start:
        shift_end += timedelta(days=1)

    # Calculate overlap between shift and fixed hours
    latest_start = max(shift_start, fixed_start)
    earliest_end = min(shift_end, fixed_end)
    overlap = (earliest_end - latest_start).total_seconds()

    morning_hours = 0.0
    extra_pay = 0.0

    # Calculate extra pay if there is overlap
    if overlap > 0:
        # Convert overlap to hours
        overlap_hours = overlap / 3600
        morning_hours = overlap_hours

        # Determine hourly rate based on Shabbat or regular shift
        if row.get('is_shabbat', False):
            hourly_rate = 32.31 * 1.5
        else:
            hourly_rate = 32.31

        extra_pay = overlap_hours * hourly_rate

    return extra_pay, morning_hours

def calculate_totals(df):
    """
    Calculates total tips, extra pay, total hours, and grand total.
    """
    total_tip = df['טיפ מזומן'].sum()
    total_completion = df['השלמה'].sum()
    total_extra_pay = df['extra_pay'].sum()
    total_morning_hours = df['morning_hours'].sum()

    # Calculate total hours worked
    total_hours_worked = df['hours_worked'].sum()

    grand_total = total_tip + total_completion + total_extra_pay

    # Calculate average hourly salary
    if total_hours_worked > 0:
        average_hourly_salary = grand_total / total_hours_worked
    else:
        average_hourly_salary = 0.0

    totals = {
        'total_tip': round(float(total_tip), 2),
        'total_completion': round(float(total_completion), 2),
        'total_extra_pay': round(float(total_extra_pay), 2),
        'total_morning_hours': round(float(total_morning_hours), 2),
        'total_hours_worked': round(float(total_hours_worked), 2),
        'average_hourly_salary': round(float(average_hourly_salary), 2)
    }

    return totals

def calculate_shift_duration(row):
    """
    Calculates the duration of a shift in hours.
    """
    # Common date for all datetime objects
    common_date = datetime(1900, 1, 1).date()

    shift_start_str = row.get('כניסה')
    shift_end_str = row.get('יציאה')

    if pd.isna(shift_start_str) or pd.isna(shift_end_str):
        return 0.0

    try:
        shift_start_time = datetime.strptime(shift_start_str, '%H:%M').time()
        shift_end_time = datetime.strptime(shift_end_str, '%H:%M').time()
    except ValueError:
        return 0.0

    # Combine times with the common date
    shift_start = datetime.combine(common_date, shift_start_time)
    shift_end = datetime.combine(common_date, shift_end_time)

    # Handle overnight shifts
    if shift_end < shift_start:
        shift_end += timedelta(days=1)

    shift_duration = (shift_end - shift_start).total_seconds() / 3600

    return shift_duration

def generate_trends(df):
    """
    Generates trends data and plots.
    """
    # Group by 'month_year' and compute sums or averages
    grouped = df.groupby('month_year').agg({
        'טיפ מזומן': 'sum',
        'השלמה': 'sum',
        'extra_pay': 'sum',
        'morning_hours': 'sum',
        'hours_worked': 'sum',
        'total_salary': 'sum'
    })

    # Calculate average hourly salary per month
    grouped['average_hourly_salary'] = grouped['total_salary'] / grouped['hours_worked']

    # Reset index to turn 'month_year' into a column
    grouped = grouped.reset_index()

    # Convert 'month_year' to string for plotting and display
    grouped['month_year_str'] = grouped['month_year'].astype(str)

    # Generate plots
    plot_files = generate_plots(grouped)

    return grouped, plot_files

def generate_plots(grouped):
    """
    Generates and saves trend plots.
    """
    # Create a folder to save plots if it doesn't exist
    plot_folder = os.path.join('static', 'plots')
    if not os.path.exists(plot_folder):
        os.makedirs(plot_folder)

    plot_files = {}

    # Generate unique IDs for filenames
    hours_worked_plot_filename = os.path.join('plots', f'hours_worked_{uuid.uuid4()}.png')
    average_salary_plot_filename = os.path.join('plots', f'average_salary_{uuid.uuid4()}.png')

    # Full paths
    hours_worked_plot = os.path.join('static', hours_worked_plot_filename)
    average_salary_plot = os.path.join('static', average_salary_plot_filename)

    # Plot hours worked over time
    plt.figure()
    plt.plot(grouped['month_year_str'], grouped['hours_worked'], marker='o')
    plt.title('Hours Worked Over Time')
    plt.xlabel('Month')
    plt.ylabel('Hours Worked')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(hours_worked_plot)
    plt.close()
    plot_files['hours_worked_plot'] = hours_worked_plot_filename

    # Plot average hourly salary over time
    plt.figure()
    plt.plot(grouped['month_year_str'], grouped['average_hourly_salary'], marker='o', color='green')
    plt.title('Average Hourly Salary Over Time')
    plt.xlabel('Month')
    plt.ylabel('Average Hourly Salary')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(average_salary_plot)
    plt.close()
    plot_files['average_salary_plot'] = average_salary_plot_filename

    return plot_files

if __name__ == '__main__':
    app.run(debug=True)