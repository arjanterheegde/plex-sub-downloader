import os
import tempfile
import time
from .subliminalHelper import SubliminalHelper
from subliminal.video import Video as SubVideo
from subliminal.subtitle import Subtitle
from .PlexWebhookEvent import PlexWebhookEvent
from .logger import Logger
from plexapi.server import PlexServer
from plexapi.video import Video
from plexapi.library import LibrarySection
from plexapi.media import SubtitleStream

log = Logger.getInstance().getLogger()

class PlexSubDownloader:

    def __init__(self):
        self.sub = None
        self.plex = None

    def configure(self, config):
        """initializes and configures the needed classes for PlexSubDownloader to work.
        :param object config: config json. See config.schema.json for structure.
        :return: True if everything initializes correctly, otherwise False.
        """

        log.info("Configuring PlexSubDownloader")
        self.config = config
        self.subtitle_destination = config.get('subtitle_destination', 'with_media')
        self.sub = SubliminalHelper(languages=config.get('languages', None), 
        providers= config.get('subtitle_providers', None),
        provider_configs=config.get('subtitle_provider_configs', None))
        
        self.plexServer = PlexServer(baseurl=config['plex_base_url'], token=config['plex_auth_token'])
        
        if config['subtitle_destination'] == 'with_media' and self.checkLibraryPermissions() == False:
            log.error("One or more of the Plex libraries are not readable/writable by the current user.")
            return False
        return True
        

    def handleWebhookEvent(self, event):
        """Handles the given webhook event. 
        :param PlexWebhookEvent event:
        """
        log.debug("handleWebhookEvent")
        log.debug("Event type: " + event.event)

        if event.event == "library.new" or event.event == "media.play":
            self.handleLibraryNewEvent(event)

    def handleLibraryNewEvent(self, event):
        """Handles webhook events of type library.new.
        Retrieves the relevent item from Plex and searches for subtitles.
        :param PlexWebhookEvent event:
        """
        log.info("Handling library.new event")
        log.info(f'Title: {event.Metadata.title}, type: {event.Metadata.type}, section: {event.Metadata.librarySectionTitle}')
        
        video = self.getVideoItemFromEvent(event)

        missingVideos = self.getVidsMissingSubtitles([video])
        log.info("Found " + str(len(missingVideos)) + " videos missing subtitles")
        log.info([f'{video.title}, {video.key}' for video in missingVideos])
        if len(missingVideos) > 0:
            subtitles = self.downloadSubtitlesForVideos(missingVideos)

            if self.subtitle_destination == "metadata":
                self.uploadSubtitlesToMetadata(missingVideos, subtitles)
            else:
                self.sub.save_subtitles(subtitles)
        else:
            log.info("No subtitles to download, doing nothing!")

    def getVideoItemFromEvent(self, event):
         # if Metadata.type == "show", then the metadata key looks like
        # `/library/metadata/45533/children`, which doesn't return what we actually want
        # when we call fetchItem
        key = event.Metadata.key
        key = key.replace("/children", "")
        video = self.plexServer.fetchItem(ekey=key)
        video.reload()
        return video


    def getVidsMissingSubtitles(self,videos):
        """Search the given list of videos for ones that don't already have subtitles.
        For videos of type 'season' or 'show', this will search through all of the episodes
        as well.
        :param list videos: list of plexapi.video.Video objects.
        :return: list of Video objects that don't have any subtitles.
        """

        vidsMissingSubs = []
        for v in videos:
            if v.type == 'movie' or v.type == 'episode':
                if self.checkVideoForSubtitles(v) == False:
                    vidsMissingSubs.append(v)
                
            elif v.type == 'season' or v.type == 'show':
                eps = v.episodes()
                for e in eps:
                    e.reload()
                    if self.checkVideoForSubtitles(e) == False:
                     vidsMissingSubs.append(e)

        return vidsMissingSubs

    def checkVideoForSubtitles(self, video):
        """Checks the given video for subtitles by retrieving the SubtitleStreams.
        Checks against the list of languages provided in config['languages']. If _any_ 
        language isn't found, this will return False.
        :param video: plexapi.video.Video object
        :return: boolean, True if the video has subtitles for every requested language, False otherwise
        """
        #added hack to ignore forced subs

        #languagesNotFound = self.config['languages']
        checkbox = False

        subs = video.subtitleStreams()
        log.debug(f'Found {len(subs)} subtitles for video {video.title} {video.key}')
        for sub in subs:
            log.debug(f'subtitle {sub.displayTitle} language code:{sub.languageCode}, format: {sub.format}, forced: {sub.forced}, display title: {sub.displayTitle}')
            #if sub.languageCode in languagesNotFound and sub.forced == False:
            if "OpenSubtitles" in sub.displayTitle or "External" in sub.displayTitle:
                checkbox = True
                break
        
        return checkbox
        
        
    def downloadSubtitlesForVideos(self, videos):
        """Attempts to download subtitles for the given list of videos.
        :param list videos: list of plexapi.video.Video objects.
        :return: dict[subliminal.video.Video, list[subliminal.subtitle.Subtitle]]
        """

        log.info("Downloading subtitles for " + str(len(videos)) + " videos")
        log.info([video.title for video in videos])
        subtitles = self.sub.search_videos(videos)
        return subtitles


    def uploadSubtitlesToMetadata(self, plexVideos, subtitles):
        """Saves the subtitles to Plex.
        :param list videos: list of plexapi.video.Video objects.
        :param dict subtitles: dict of dict[subliminal.video.Video, list[subliminal.subtitle.Subtitle]]
        """

        log.info("Saving subtitles to Plex metadata")
        tempdir = tempfile.gettempdir()
        for video in plexVideos:
            mediaPart = video.media[0].parts[0]
            filepath = video.media[0].parts[0].file
            for subVideo, subtitles in subtitles.items():
                if subVideo.name == filepath:
                    log.info(f'found {len(subtitles)} for video {subVideo.name}')

                    savedSubtitlePaths = self.sub.save_subtitle(subVideo, subtitles, destination=tempdir)
                    for subtitlePath in savedSubtitlePaths:
                        #log.info(f"Cleaning subtitles")
                        #time.sleep(5)
                        #os.system('/usr/local/bin/python3.10 /home/pi/.local/bin/subclean ' + subtitlePath + ' --overwrite >> /home/pi/plex-sub/log.log')
                        log.info(f'Uploading subtitles \'{subtitlePath}\' to video {video.title} {video.key}')
                        originalDefault = None 
                        existingSubs = video.subtitleStreams();
                        for sub in existingSubs:
                            if sub.selected:
                                originalDefault = sub
                                break

                        video.uploadSubtitles(subtitlePath)
                        if originalDefault is not None:
                            mediaPart.setDefaultSubtitleStream(originalDefault)
                        else:
                            mediaPart.resetDefaultSubtitleStream()
                        


    def checkLibraryPermissions(self, sectionId=None):
        """Checks whether the application has permissions to read/write to the base paths of each section 
        within Plex's library.
        :param string sectionId: An optional id value to just check permissions of a single section.
        :return: True if all sections are read/writeable, otherwise False.
        """

        log.debug("Checking library permissions")
        sections = []

        checkedOk = True
        if sectionId != None:
            sections = [self.plexServer.library.sectionByID(sectionId)]
        else:
            sections = self.plexServer.library.sections()

        for section in sections:
            locations = section.locations
            for location in locations:
                exists = os.path.exists(location)
                if not exists:
                    log.error(f'Error checking library permissions. Directory \'{location}\' doesnt exist?')
                    checkedOk = False
                else:
                    read_access = os.access(location, os.R_OK)
                    write_access = os.access(location, os.W_OK)
                    if not read_access or not write_access:
                        log.error(f'Error checking library permissions. Cannot read/write to directory \'{location}\'')
                        checkedOk = False
        
        return checkedOk


