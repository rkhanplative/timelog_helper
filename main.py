import os
import argparse
from typing import List, Dict
from github import Github, Repository, Commit
from jira import JIRA, Issue
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import html

class ActivityReporter:
    def __init__(self, start_date: str, end_date: str):
        """
        Initialize the ActivityReporter with a date range.

        :param start_date: Start date for the activity report in 'YYYY-MM-DD' format.
        :param end_date: End date for the activity report in 'YYYY-MM-DD' format.
        """
        self.start_date = datetime.strptime(start_date, '%Y-%m-%d')
        self.end_date = datetime.strptime(end_date, '%Y-%m-%d')
        self.start_timestamp = self.start_date.timestamp()
        self.end_timestamp = self.end_date.timestamp()
        self.root = ET.Element("activity_report")

    def fetch_github_activity(self) -> None:
        """
        Fetch GitHub activity and add it to the XML report with detailed diffs.
        """
        github_token = os.getenv('GITHUB_TOKEN')
        repo_name = "plative/plaito"

        g = Github(github_token)

        try:
            repo: Repository.Repository = g.get_repo(repo_name)
            commits: List[Commit.Commit] = list(repo.get_commits(
                since=self.start_date, until=self.end_date))
            
            # sort commits by date
            commits.sort(key=lambda x: x.commit.author.date if x.commit.author.date else x.commit.committer.date, reverse=True)

            github_activity = ET.SubElement(self.root, "github_activity")

            for commit in commits:
                commit_element = ET.SubElement(github_activity, "commit")
                ET.SubElement(commit_element, "sha").text = commit.sha
                ET.SubElement(commit_element, "author").text = html.escape(commit.commit.author.name)
                ET.SubElement(commit_element, "date").text = commit.commit.author.date.isoformat()
                ET.SubElement(commit_element, "message").text = html.escape(commit.commit.message).replace("\n", "\\n")
                ET.SubElement(commit_element, "url").text = commit.html_url

                # Add file diffs
                files_changed = ET.SubElement(commit_element, "files_changed")
                for file in commit.files:
                    file_element = ET.SubElement(files_changed, "file")
                    ET.SubElement(file_element, "filename").text = html.escape(file.filename)
                    ET.SubElement(file_element, "additions").text = str(file.additions)
                    ET.SubElement(file_element, "deletions").text = str(file.deletions)
                    ET.SubElement(file_element, "changes").text = str(file.changes)
                    ET.SubElement(file_element, "status").text = file.status
                    # Include the diff patch (truncated for readability)
                    diff_patch = file.patch if len(file.patch) < 1000 else file.patch[:1000] + "... [truncated]"
                    ET.SubElement(file_element, "diff").text = html.escape(diff_patch).replace("\n", "\\n")

        except Exception as e:
            print(f"Error fetching GitHub activity: {e}")

    def fetch_jira_activity(self) -> None:
        """
        Fetch Jira activity and add it to the XML report.
        """
        jira_email = os.getenv('JIRA_EMAIL')
        jira_token = os.getenv('JIRA_TOKEN')
        jira_server = os.getenv('JIRA_SERVER')

        jira = JIRA(server=jira_server, basic_auth=(jira_email, jira_token))

        start_date_str = self.start_date.strftime('%Y-%m-%d')
        end_date_str = self.end_date.strftime('%Y-%m-%d')

        jql_query = f'updated >= "{start_date_str}" AND updated <= "{end_date_str}" AND assignee = "rkhan@plative.com"'

        try:
            issues: List[Issue] = jira.search_issues(jql_query)

            jira_activity = ET.SubElement(self.root, "jira_activity")

            for issue in issues:
                formatted_summary = html.escape(issue.fields.summary).replace("\n", "\\n")

                issue_element = ET.SubElement(jira_activity, "issue")
                ET.SubElement(issue_element, "key").text = issue.key
                ET.SubElement(issue_element, "summary").text = formatted_summary
                ET.SubElement(issue_element, "status").text = issue.fields.status.name
                ET.SubElement(issue_element, "updated").text = issue.fields.updated
                ET.SubElement(issue_element, "assignee").text = html.escape(issue.fields.assignee.displayName)
                ET.SubElement(issue_element, "url").text = f"{jira_server}/browse/{issue.key}"

        except Exception as e:
            print(f"Error fetching Jira activity: {e}")

    def fetch_gmail_activity(self) -> None:
        """
        Fetch Salesforce-related emails from Gmail and add them to the XML report, including all emails within a thread.
        """
        # Load credentials from the token file
        creds = Credentials.from_authorized_user_file('token.json_rkhan@plative.com', ['https://www.googleapis.com/auth/gmail.readonly'])

        # Connect to the Gmail API
        service = build('gmail', 'v1', credentials=creds)

        gmail_activity = ET.SubElement(self.root, "gmail_activity")

        # Search for emails from the last month containing 'Salesforce' in the subject
        query = f'from:system@salesforce.com after:{self.start_date.strftime("%Y/%m/%d")} before:{self.end_date.strftime("%Y/%m/%d")}'
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])

        if not messages:
            print('No emails found.')
            return

        # Track processed thread IDs to avoid duplicates
        processed_threads = set()

        # Retrieve and process each email
        for message in messages:
            msg = service.users().messages().get(userId='me', id=message['id']).execute()

            # Get the thread ID and fetch all messages in the thread
            thread_id = msg['threadId']
            if thread_id in processed_threads:
                continue  # Skip this thread if already processed

            thread_messages = service.users().threads().get(userId='me', id=thread_id).execute().get('messages', [])
            processed_threads.add(thread_id)

            # Process each message in the thread
            for thread_message in thread_messages:
                subject = ''
                for header in thread_message['payload']['headers']:
                    if header['name'] == 'Subject':
                        subject = header['value']

                snippet = thread_message.get('snippet', '')

                email_element = ET.SubElement(gmail_activity, "email")
                ET.SubElement(email_element, "subject").text = html.escape(subject).replace('\n','\\n')
                ET.SubElement(email_element, "snippet").text = html.escape(snippet).replace('\n','\\n')

    def incorporate_slack_context(self, slack_context_file: str) -> None:
        """
        Incorporate the Slack context XML from a file into the main XML report.
        
        :param slack_context_file: The file containing the Slack context in XML format.
        """
        try:
            # Parse the slack_context.txt file and get its root
            slack_context_tree = ET.parse(slack_context_file)
            slack_context_root = slack_context_tree.getroot()

            # Add the Slack context to the main XML report
            self.root.append(slack_context_root)

        except Exception as e:
            print(f"Error incorporating Slack context: {e}")

    def generate_xml_report(self) -> str:
        """
        Generate a pretty-printed XML report from the fetched activities.

        :return: The XML report as a string.
        """
        xml_string = ET.tostring(self.root, encoding='utf-8')
        pretty_xml = minidom.parseString(xml_string).toprettyxml(indent="    ")
        return pretty_xml

    def run(self, slack_context_file: str) -> None:
        """
        Run the activity reporter to fetch data from GitHub, Jira, Gmail, incorporate Slack context, and generate an XML report.
        
        :param slack_context_file: The file containing the Slack context in XML format.
        """
        self.fetch_github_activity()
        self.fetch_jira_activity()
        self.fetch_gmail_activity()

        report = self.generate_xml_report()

        # Output the XML report to a file
        with open("context.xml", "w") as output_file:
            output_file.write(report)
        print("Report generated and saved to context.xml")


def parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.

    :return: Parsed arguments as a Namespace object.
    """
    parser = argparse.ArgumentParser(description="Generate an XML report of GitHub, Jira, Gmail activities, and incorporate Slack context.")
    parser.add_argument('--start_date', type=str, required=True, help="Start date for the activity report in 'YYYY-MM-DD' format.")
    parser.add_argument('--end_date', type=str, required=True, help="End date for the activity report in 'YYYY-MM-DD' format.")
    parser.add_argument('--slack_context_file', type=str, required=True, help="Path to the file containing the Slack context in XML format.")
    return parser.parse_args()


if __name__ == "__main__":
    # Parse command-line arguments
    args = parse_arguments()

    # Initialize and run the activity reporter
    reporter = ActivityReporter(start_date=args.start_date, end_date=args.end_date)
    reporter.run(args.slack_context_file)
